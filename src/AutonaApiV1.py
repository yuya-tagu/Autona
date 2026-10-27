"""External Control API (`/api/v1/`).

A thin, structured HTTP namespace added to the existing Flask + SocketIO
server (ADR-001 D3). Every handler delegates to the operations the legacy
routes use, so state logic is never duplicated: ``AutonaWebOperations`` for
what is core's (both shells), ``WebServerActions`` for what still needs a
board or panel widget (the desktop only -- headless, those answer 503
UNAVAILABLE, ADR-005 S3c). Existing routes (``/score``,
``/scoreboard<N>-...``) and Socket.IO events are left untouched.

Spec (source of truth): ``.claude/design/external-control-api.md``.

Sections below mirror the implementation tickets:
  - foundation + read endpoints  (docs/tasks/implement-external-control-api-readonly.md)
  - safe write operations        (docs/tasks/implement-external-control-api-safe-ops.md)
  - guarded operations           (docs/tasks/implement-external-control-api-guarded-ops.md)
"""

import functools
import json
import os
import re
import threading
import time
import traceback
from collections import OrderedDict

from flask import Blueprint, jsonify, request
from loguru import logger

from .core.StateManager import StateManager
from .core.Actions import AutonaActions
from .core import StateSchema as schema
from .core import LayoutCatalog
from .core import Top8Service
from .core.GameAssetCatalog import GameAssetCatalog
from .core.CountryCatalog import CountryCatalog
# Import order mirrors the singleton init order (agent-pitfalls #2): the asset
# manager must be importable before the tournament data provider. By the time
# this module loads both singletons already exist (WebServerActions imported
# them first), so this is only defensive.
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .Helpers import AutonaQtHelper
from . import AutonaWebOperations as WebOps
from .AutonaWebOperations import NotAvailable

# ── Blueprint ────────────────────────────────────────────────────────────
api_v1 = Blueprint("api_v1", __name__, url_prefix="/api/v1")

# Dedicated logger for write operations (spec §2.7 / D7 A-1). Reads are not
# logged here. Bound so the channel can be filtered downstream.
_oplog = logger.bind(channel="api_v1_ops")

# Reference to the live WebServerActions instance, injected at registration
# time so this module never imports AutonaWebServer (avoids a circular import).
# None after init_api_v1(None): the headless shell, which has no board-
# dependent operations (see _actions_ref).
_actions = None
_initialized = False

_version_cache = None


# ── Error model (spec §2.6) ──────────────────────────────────────────────
INVALID_ARGUMENT = "INVALID_ARGUMENT"        # 400
NOT_FOUND = "NOT_FOUND"                       # 404
CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"  # 409
CONFLICT = "CONFLICT"                         # 409
NOT_READY = "NOT_READY"                       # 409
UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"         # 504
INTERNAL = "INTERNAL"                         # 500
# A board-dependent operation in the headless shell (ADR-005 S3c): the
# server runs, the operation needs a widget it does not have. Not retryable.
UNAVAILABLE = "UNAVAILABLE"                   # 503


class ApiError(Exception):
    """Raised by handlers; converted to an error envelope by the wrappers."""

    def __init__(self, code, message, status):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# ── Response envelopes (spec §2.4 / §2.5) ────────────────────────────────
def ok(data=None, revision=None, status=200):
    """Success envelope: ``{"ok": true, "data": ..., "revision": ...}``.

    ``data`` / ``revision`` are omitted when None.
    """
    body = {"ok": True}
    if data is not None:
        body["data"] = data
    if revision is not None:
        body["revision"] = revision
    resp = jsonify(body)
    resp.status_code = status
    return resp


def err(code, message, status):
    """Error envelope: ``{"ok": false, "error": {"code", "message"}}``."""
    resp = jsonify({"ok": False, "error": {"code": code, "message": message}})
    resp.status_code = status
    return resp


# ── Validation / parsing helpers ─────────────────────────────────────────
def validate_scoreboard(n):
    """Reads: scoreboard number must be 1, 2 or 3 (spec §2.12).

    Pre-validating here prevents the most common downstream failure:
    GetScoreboard(n)=None followed by an AttributeError (spec §14.2).
    """
    if n not in (1, 2, 3):
        raise ApiError(INVALID_ARGUMENT, "scoreboard must be 1, 2 or 3", 400)
    return n


def require_board_12(n):
    """Write ops: only board 1 (1v1) and 2 (team) are in scope.

    Board 3 (crew) is a different widget class with different controls and is
    out of initial scope (spec §1.1 / §2.12); board1/2-only operations are
    explicitly rejected on it rather than crashing.
    """
    validate_scoreboard(n)
    if n == 3:
        raise ApiError(
            INVALID_ARGUMENT,
            "crew board (3) does not support this operation",
            400,
        )
    return n


def _validate_team(t):
    if t not in (1, 2):
        raise ApiError(INVALID_ARGUMENT, "team must be 1 or 2", 400)
    return t


def _require_int(value, name):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ApiError(INVALID_ARGUMENT, f"{name} must be an integer", 400)


def _json_body():
    """Parse the JSON request body as an object (or {} when absent).

    ``force=True`` parses the body even without a ``Content-Type:
    application/json`` header — integration clients (e.g. the OBS Advanced
    Scene Switcher HTTP action) don't always send one reliably.
    """
    data = request.get_json(force=True, silent=True)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ApiError(INVALID_ARGUMENT, "request body must be a JSON object", 400)
    return data


def _truthy(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?$")
_STARTGG_RE = re.compile(r"start\.gg/tournament/[^/]+/event[s]?/[^/]+")


def current_revision():
    """Best-effort current state revision (spec §14.1 / §14.1a)."""
    return StateManager.revision


def _actions_ref():
    """The board-dependent operations (WebServerActions).

    NOT_READY before the server has initialized the API; NotAvailable (503
    UNAVAILABLE) when it did so without them -- the headless shell.
    """
    if _actions is None:
        if _initialized:
            raise NotAvailable()
        raise ApiError(NOT_READY, "API not initialized", 409)
    return _actions


def _app_version():
    global _version_cache
    if _version_cache is None:
        try:
            with open("./assets/versions.json", encoding="utf-8") as f:
                _version_cache = json.load(f).get("program", "?")
        except Exception:
            _version_cache = "?"
    return _version_cache


# ── Handler wrappers ─────────────────────────────────────────────────────
def api_handler(fn):
    """Read endpoints: convert ApiError / unexpected exceptions to envelopes."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ApiError as e:
            return err(e.code, e.message, e.status)
        except NotAvailable as e:
            return err(UNAVAILABLE, e.message, 503)
        except Exception:
            logger.error(traceback.format_exc())
            return err(INTERNAL, "internal error", 500)

    return wrapper


def _safe_params(keys_only=False):
    # Most write bodies carry only small scalar params (no PII / secrets).
    # Truncate defensively all the same. Bodies that can carry personal data
    # (the player PATCH: tag, real name, social handle) are logged by key
    # only (spec §2.7: no PII in the op log).
    body = request.get_json(force=True, silent=True)
    if not body:
        return {}
    if keys_only:
        if isinstance(body, dict):
            return repr(sorted(body.keys()))
        return "<non-object body>"
    text = repr(body)
    return text if len(text) <= 300 else text[:300] + "…"


def write_op(operation, log_keys_only=False):
    """Write endpoints: exception->envelope conversion plus an INFO op-log
    line recording IP / method / path / params / result (spec §2.7)."""

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            params = _safe_params(keys_only=log_keys_only)
            try:
                resp = fn(*args, **kwargs)
                status = getattr(resp, "status_code", 200)
                _oplog.info(
                    f"{request.remote_addr} {request.method} {request.path} "
                    f"op={operation} params={params} -> {status}"
                )
                return resp
            except ApiError as e:
                _oplog.info(
                    f"{request.remote_addr} {request.method} {request.path} "
                    f"op={operation} params={params} -> {e.status} {e.code}"
                )
                return err(e.code, e.message, e.status)
            except NotAvailable as e:
                _oplog.info(
                    f"{request.remote_addr} {request.method} {request.path} "
                    f"op={operation} params={params} -> 503 {UNAVAILABLE}"
                )
                return err(UNAVAILABLE, e.message, 503)
            except Exception:
                logger.error(traceback.format_exc())
                _oplog.info(
                    f"{request.remote_addr} {request.method} {request.path} "
                    f"op={operation} params={params} -> 500 INTERNAL"
                )
                return err(INTERNAL, "internal error", 500)

        return wrapper

    return deco


# ── Idempotency cache (spec §2.9 / §2.10) ────────────────────────────────
class _IdempotencyCache:
    """Process-local, Lock-protected short-lived cache of applied keys.

    Relative ops (score:adjust) are not idempotent; a repeated Idempotency-Key
    is applied only once. Bounded by item count and TTL.
    """

    def __init__(self, max_items=256, ttl=60.0):
        self._lock = threading.Lock()
        self._store = OrderedDict()  # key -> monotonic timestamp
        self._max = max_items
        self._ttl = ttl

    def claim(self, key):
        """Atomically claim ``key``. True if first time, False if already seen."""
        with self._lock:
            self._evict()
            if key in self._store:
                return False
            self._store[key] = time.monotonic()
            self._store.move_to_end(key)
            while len(self._store) > self._max:
                self._store.popitem(last=False)
            return True

    def release(self, key):
        with self._lock:
            self._store.pop(key, None)

    def _evict(self):
        now = time.monotonic()
        stale = [k for k, t in self._store.items() if now - t > self._ttl]
        for k in stale:
            self._store.pop(k, None)


_idem = _IdempotencyCache()


# ── Heavy-op in-progress guard (spec §2.8 / §2.10) ───────────────────────
class _HeavyOpGuard:
    """Rejects concurrent duplicate heavy operations with 409 CONFLICT.

    The flag is held until the operation's own task clears it when it is
    over, whichever way it went (the GUI-thread task for the tournament,
    the asset loader's ``done`` for the game), and auto-expires
    after ``ttl`` as a safety net so a missed completion never sticks the
    flag forever. Completion is authoritatively observed by clients via WS
    revision progress (spec §2.8).

    ``ttl`` is a generous backstop, not the primary clear: a full game asset
    reload can take ~60-70s, so it must comfortably exceed the slowest heavy
    op or the flag would expire mid-operation and the completion (the
    loader's ``done``, for the game) would never be the effective clear.

    Not the bracket refresh's: since ADR-005 S3e-8 the Top 8 import keeps
    its own in-progress flag in core (Top8Service), shared by every way in.
    """

    def __init__(self, ttl=120.0):
        self._lock = threading.Lock()
        self._inflight = {}  # op key -> start monotonic
        self._ttl = ttl

    def try_begin(self, op):
        with self._lock:
            now = time.monotonic()
            started = self._inflight.get(op)
            if started is not None and (now - started) < self._ttl:
                return False
            self._inflight[op] = now
            return True

    def end(self, op):
        with self._lock:
            self._inflight.pop(op, None)


_heavy = _HeavyOpGuard()


# ── Initialization (called from AutonaWebServer.__init__) ────────────────
def init_api_v1(actions):
    """Bind the live WebServerActions (None in the headless shell).

    Every heavy op releases its in-progress flag itself (see
    _HeavyOpGuard), so there are no completion signals to wire here.
    """
    global _actions, _initialized
    _actions = actions
    _initialized = True


# ═══════════════════════════════════════════════════════════════════════
# Read endpoints (spec §5 — GET, not op-logged)
# ═══════════════════════════════════════════════════════════════════════
@api_v1.route("/state")
@api_handler
def state():
    full = WebOps.program_state()  # {'state','revision','delta_index'}
    return ok(data=full.get("state"), revision=full.get("revision"))


@api_v1.route("/health")
@api_handler
def health():
    provider = AutonaTournamentDataProvider.instance.GetProvider()
    game = StateManager.Get("game") or {}
    current_game = game.get("codename") if isinstance(game, dict) else None
    # Same resolver the hotkeys and the window use, rather than a third copy
    # of the format->board table: it also carries the legacy
    # `scoreboard.team_mode` fallback, so a settings file predating
    # `battle_format` no longer reports activeBoard 1 while the window shows
    # the team board (headless core Step 3).
    active_board = AutonaActions.instance.ActiveBoard()
    try:
        out_mtime = int(os.path.getmtime("./out/program_state.json"))
    except OSError:
        out_mtime = None
    data = {
        "status": "ok",
        "version": _app_version(),
        "revision": StateManager.revision,
        "currentGame": current_game,
        "activeBoard": active_board,
        "tournamentLoaded": provider is not None,
        "outLastWriteUnix": out_mtime,
    }
    return ok(data=data, revision=StateManager.revision)


@api_v1.route("/scoreboards/<int:n>")
@api_handler
def get_scoreboard(n):
    validate_scoreboard(n)
    board_state = WebOps.get_scoreboard(n)
    data = dict(board_state) if isinstance(board_state, dict) else {"state": board_state}
    # The live swap flag (score.{n}.teamsSwapped for boards 1/2 -- not the
    # committed view above; CrewBattleService's for board 3, in both shells
    # since ADR-005 S3e-2), read through the legacy operation so both routes
    # answer the same. Tolerate a board that cannot answer.
    try:
        data["swapped"] = WebOps.get_swap(n) == "True"
    except Exception:
        pass
    return ok(data=data, revision=current_revision())


@api_v1.route("/games")
@api_handler
def get_games():
    return ok(data=WebOps.get_games())


@api_v1.route("/games/current")
@api_handler
def get_current_game():
    return ok(data=WebOps.get_current_game())


@api_v1.route("/match-names")
@api_handler
def get_match_names():
    return ok(data=WebOps.get_match_names())


@api_v1.route("/characters")
@api_handler
def get_characters():
    return ok(data=WebOps.get_characters())


@api_v1.route("/variants")
@api_handler
def get_variants():
    return ok(data=WebOps.get_variants())


@api_v1.route("/sets")
@api_handler
def get_sets():
    # `get_sets` issues a synchronous start.gg call (GetMatches) that can hang
    # or raise (agent-pitfalls #11); provider-null is already guarded to [].
    want_finished = request.args.get("finished") or request.args.get("getFinished")
    args = {}
    if want_finished is not None and str(want_finished).lower() not in ("0", "false", "no"):
        args = {"getFinished": True}
    try:
        return ok(data=WebOps.get_sets(args))
    except ApiError:
        raise
    except Exception:
        logger.error(traceback.format_exc())
        raise ApiError(UPSTREAM_TIMEOUT, "start.gg request failed", 504)


@api_v1.route("/sets/<setId>")
@api_handler
def get_match(setId):
    sid = _require_int(setId, "setId")  # int() failure -> 400
    provider = AutonaTournamentDataProvider.instance.GetProvider()
    if provider is None:
        raise ApiError(NOT_READY, "no tournament loaded", 409)
    try:
        return ok(data=WebOps.get_match(sid))
    except ApiError:
        raise
    except Exception:
        logger.error(traceback.format_exc())
        raise ApiError(UPSTREAM_TIMEOUT, "start.gg request failed", 504)


@api_v1.route("/commentary")
@api_handler
def get_commentary():
    return ok(data=WebOps.get_comms())


@api_v1.route("/countries/<code>/states")
@api_handler
def get_country_states(code):
    # Country data is keyed by upper-case ISO code ("JP"). It is loaded while
    # the window is built (or by the first player widget), so before that
    # every code would read as unknown -- answer NOT_READY rather than a
    # misleading 404.
    if not CountryCatalog.instance.IsLoaded():
        raise ApiError(NOT_READY, "country data is not loaded yet", 409)
    code = code.upper()
    # The legacy /states?countryCode= answers an unknown code with None,
    # which Flask turns into a 500; here it is a 404.
    states = WebOps.get_states(code)
    if states is None:
        raise ApiError(NOT_FOUND, f"unknown country code: {code}", 404)
    return ok(data=states)


@api_v1.route("/layouts")
@api_handler
def get_layouts():
    # The layout catalog the Board / Commentary / Top 8 tabs and the Designer
    # list from (src/core/LayoutCatalog.py). Not state, so no revision and
    # no WebServerActions hop: the catalog is already the single reader.
    # ?kind= takes one kind, a comma-separated list, or a repeated parameter.
    kinds = [kind.strip() for raw in request.args.getlist("kind")
             for kind in raw.split(",") if kind.strip()]
    unknown = sorted(set(kinds) - set(LayoutCatalog.KINDS))
    if unknown:
        raise ApiError(
            INVALID_ARGUMENT,
            f"unknown kind: {', '.join(unknown)} "
            f"(expected one of: {', '.join(LayoutCatalog.KINDS)})",
            400,
        )
    return ok(data=LayoutCatalog.Layouts(kinds))


# ═══════════════════════════════════════════════════════════════════════
# Safe write operations (spec §6 — op-logged, no confirm)
# ═══════════════════════════════════════════════════════════════════════
@api_v1.route("/scoreboards/<int:n>/teams/<int:t>/score:adjust", methods=["POST"])
@write_op("score.adjust")
def score_adjust(n, t):
    require_board_12(n)
    _validate_team(t)
    body = _json_body()
    if "delta" not in body:
        raise ApiError(INVALID_ARGUMENT, "delta is required", 400)
    delta = _require_int(body.get("delta"), "delta")

    def apply():
        # |delta| single-step adjustments (spec §6.1). Synchronous since
        # headless core Step 3 -- the actions write the state on this thread
        # instead of queueing a Qt signal into a board widget. The response
        # revision below is still the *published* one, which a board 1/2
        # score does not move until 反映 (R-Sync-11).
        if delta >= 0:
            for _ in range(delta):
                WebOps.team_scoreup(n, t)
        else:
            for _ in range(-delta):
                WebOps.team_scoredown(n, t)

    key = request.headers.get("Idempotency-Key")
    if key:
        if _idem.claim(key):
            try:
                apply()
            except Exception:
                _idem.release(key)
                raise
        # else: duplicate key — already applied, skip (spec §2.9)
    else:
        apply()
    # Held subtree: the edit is applied but not published, so this revision
    # is the last published one; the client sees the score after 反映 (§14.1a).
    return ok(revision=current_revision())


@api_v1.route("/scoreboards/<int:n>/score:reset", methods=["POST"])
@write_op("score.reset")
def score_reset(n):
    require_board_12(n)
    # Boards 1/2 only (require_board_12), so this is AutonaActions.ResetScore:
    # a held write, applied synchronously but not published. The revision
    # below is therefore the last published one and does not move until 反映
    # -- same as score:adjust above, not the "accurate after the GUI hop"
    # case the crew path would have been.
    WebOps.reset_scores(n)
    return ok(revision=current_revision())


@api_v1.route("/scoreboards/<int:n>/stats:refresh", methods=["POST"])
@write_op("stats.refresh")
def stats_refresh(n):
    # Board 3 (crew) has no stats util at all, so it is rejected like every
    # other board1/2-only operation rather than silently doing nothing.
    require_board_12(n)
    # @gui_thread_sync, but the start.gg fetches it kicks off are async: the
    # revision here only covers the synchronous part (spec §14.1a) — clients
    # observe the finished stats via the WS delta stream.
    _actions_ref().refresh_stats(n)
    return ok(revision=current_revision())


@api_v1.route("/scoreboards/<int:n>:swap-teams", methods=["POST"])
@write_op("swap-teams")
def swap_teams(n):
    require_board_12(n)
    WebOps.swap_teams(n)
    return ok(revision=current_revision())


@api_v1.route("/scoreboards/<int:n>:apply", methods=["POST"])
@write_op("apply")
def apply_changes(n):
    require_board_12(n)
    WebOps.apply_changes(n)  # synchronous core write -> accurate
    return ok(revision=current_revision())


@api_v1.route("/scoreboards/<int:n>/teams/<int:t>/color", methods=["POST"])
@write_op("team.color")
def team_color(n, t):
    require_board_12(n)
    _validate_team(t)
    body = _json_body()
    color = body.get("color")
    if not isinstance(color, str) or not _COLOR_RE.match(color):
        raise ApiError(INVALID_ARGUMENT, "color must be a hex string like #FF0000", 400)
    WebOps.team_color(n, t, color)
    return ok(revision=current_revision())


@api_v1.route("/scoreboards/<int:n>/set", methods=["PATCH"])
@write_op("set.update")
def set_update(n):
    require_board_12(n)
    body = _json_body()
    kwargs = {}
    if "bestOf" in body:
        kwargs["bestOf"] = _require_int(body["bestOf"], "bestOf")
    if "phase" in body:
        kwargs["phase"] = body["phase"]
    if "pool" in body:
        kwargs["pool"] = body["pool"]
    if "match" in body:
        kwargs["match"] = body["match"]
    if "players" in body:
        kwargs["players"] = _require_int(body["players"], "players")
    if "characters" in body:
        kwargs["characters"] = _require_int(body["characters"], "characters")
    losers = None
    if "losers" in body:
        flag = body["losers"]
        if not isinstance(flag, dict):
            raise ApiError(INVALID_ARGUMENT, "losers must be an object {team,value}", 400)
        losers = (_validate_team(flag.get("team")), _truthy(flag.get("value")))
    team_name = None
    if "teamName" in body:
        named = body["teamName"]
        if not isinstance(named, dict):
            raise ApiError(INVALID_ARGUMENT, "teamName must be an object {team,value}", 400)
        named_team = _validate_team(named.get("team"))
        value = named.get("value")
        if not isinstance(value, str):
            raise ApiError(INVALID_ARGUMENT, "teamName.value must be a string", 400)
        team_name = (named_team, value)

    def apply():
        # Every v1 team parameter names the *state* team (the column the
        # operator sees), as score:adjust and color do. The losers flag is
        # therefore set_team_losers (AutonaActions.SetTeamLosers, plus what
        # the old set-shaped write did besides -- see there), not
        # set_route's losers, which SetLoadService.ApplyLosers runs through
        # DisplayTeam() and which had to be flipped back here on a swapped
        # board until ADR-005 S2b. teamName needs nothing: set_route writes
        # it into the literal team{N}column.
        #
        # Applied in the order the legacy queries apply them: the rest, then
        # the losers flag, then the team name. That order is why the name
        # goes in a call of its own. One task through the board host, so no
        # other board operation (a swap, a clear, another PATCH set) lands
        # between the calls: the desktop runs it on the GUI thread, the
        # headless host under its re-entrant lock (the calls inside run
        # there again). Core-direct writes (score:adjust, a colour) are not
        # board operations and can land in between in either shell, as
        # they always could.
        if kwargs or (losers is None and team_name is None):
            WebOps.set_route(n, **kwargs)  # synchronous -> revision accurate
        if losers is not None:
            WebOps.set_team_losers(n, *losers)
        if team_name is not None:
            WebOps.set_route(n, team=team_name[0], teamName=team_name[1])

    WebOps.boardHost.Run(apply)
    return ok(revision=current_revision())


@api_v1.route("/scoreboards/<int:n>/score:set", methods=["POST"])
@write_op("score.set")
def score_set(n):
    require_board_12(n)
    body = _json_body()
    for key in ("team1", "team2"):
        if key not in body:
            raise ApiError(INVALID_ARGUMENT, f"{key} is required", 400)
    team1 = _require_int(body["team1"], "team1")
    team2 = _require_int(body["team2"], "team2")
    # The v1 counterpart of the legacy POST /score (Web UI migration,
    # ADR-001 Addendum 2026-10-21), but not its action: post_score is a
    # set-shaped write that flips the teams on a swapped board, obeys
    # general.disable_scoreupdate and blanks match_en. This writes the two
    # *state* teams (AutonaActions.SetScore), clamped, in one save cycle.
    WebOps.set_scores(n, team1, team2)
    # Held subtree, like score:adjust: the revision is the last published one.
    return ok(revision=current_revision())


# Player fields the player PATCH accepts: what the Web UI player card sends.
# The operation applies a field only when it is non-empty (an update:
# PlayerService.ApplyData with clear=False), so a blank value leaves the
# current one in place -- same as the legacy update-team route. A sponsor or
# tag that names another player empties the id and the city (ADR-005 S3e-4).
_PLAYER_STRING_FIELDS = (
    "gamerTag", "prefix", "name", "twitter", "custom_textbox",
    "country_code", "state_code",
)


_MAINS_SHAPE = "{gameCodename: [[name: str, skin: int, variant?: str], ...]}"


def _validate_mains(mains):
    """`mains` in the one shape SetData reads per game, checked entry by entry.

    SetData (clear=False) looks up the current game's list in the object and
    reads each entry as [en_name, skin, variant]. A malformed entry used to
    raise inside the widget write -- after the other fields had already been
    applied -- so it is refused up front instead. (SetData's other, flat
    ["Mario", skin] form is a legacy single-character payload; v1 does not
    take it.)
    """
    if not isinstance(mains, dict):
        raise ApiError(INVALID_ARGUMENT, f"mains must be {_MAINS_SHAPE}", 400)
    for codename, entries in mains.items():
        if not isinstance(entries, list):
            raise ApiError(
                INVALID_ARGUMENT, f"mains.{codename} must be a list", 400)
        for entry in entries:
            if not (isinstance(entry, list) and 2 <= len(entry) <= 3
                    and isinstance(entry[0], str)
                    and isinstance(entry[1], int)
                    and not isinstance(entry[1], bool)
                    and entry[1] >= 0
                    and (len(entry) == 2 or isinstance(entry[2], str))):
                raise ApiError(
                    INVALID_ARGUMENT,
                    f"mains.{codename} entries must be "
                    "[name: str, skin: int >= 0, variant?: str]", 400)


@api_v1.route(
    "/scoreboards/<int:n>/teams/<int:t>/players/<int:p>", methods=["PATCH"])
@write_op("player.update", log_keys_only=True)
def player_update(n, t, p):
    require_board_12(n)
    _validate_team(t)
    if p < 1:
        raise ApiError(INVALID_ARGUMENT, "player must be 1 or greater", 400)
    body = _json_body()
    for key, value in body.items():
        if key in _PLAYER_STRING_FIELDS:
            if value is not None and not isinstance(value, str):
                raise ApiError(INVALID_ARGUMENT, f"{key} must be a string", 400)
        elif key == "mains":
            _validate_mains(value)
        elif key != "savePlayerToDb":
            raise ApiError(INVALID_ARGUMENT, f"unknown player field: {key}", 400)
    if body:
        data = dict(body)
        if "savePlayerToDb" in data:
            data["savePlayerToDb"] = _truthy(data["savePlayerToDb"])
        # The player write of the legacy update-team route without the rest of
        # its set payload (which blanks match_en). Addresses the state team
        # directly (no swap flip). Unlike the legacy route, which logs and
        # answers "OK" whatever happened, the outcome comes back: a slot the
        # state team does not have is a 404 (ADR-005 S3e-4: the state's
        # slots, so headless too), and an exception from the write itself is
        # turned into 500 INTERNAL by write_op. On the desktop the subtree is
        # held: the revision is the last published one.
        if not WebOps.update_player(n, t, p, data):
            raise ApiError(
                NOT_FOUND, f"team {t} has no player {p}", 404)
    return ok(revision=current_revision())


@api_v1.route("/display-options", methods=["PATCH"])
@write_op("display-options.update")
def display_options_update():
    body = _json_body()
    if not body:
        raise ApiError(
            INVALID_ARGUMENT, "at least one display option is required", 400)
    # Validate every key and value before applying any, so a bad entry
    # changes nothing. Values are JSON booleans only: the legacy route's
    # bool() made the string "false" switch an option *on*.
    unknown = sorted(k for k in body if k not in schema.DISPLAY_OPTION_KEYS)
    if unknown:
        raise ApiError(
            INVALID_ARGUMENT, f"unknown display option: {', '.join(unknown)}", 400)
    notBool = sorted(k for k, v in body.items() if not isinstance(v, bool))
    if notBool:
        raise ApiError(
            INVALID_ARGUMENT,
            f"display option values must be true or false: {', '.join(notBool)}",
            400)
    for key, value in body.items():
        result = WebOps.set_display_option({"option": key, "value": value})
        if result != "OK":
            raise ApiError(INVALID_ARGUMENT, str(result), 400)
    # display_options is not held: the write is published synchronously.
    return ok(revision=current_revision())


# ═══════════════════════════════════════════════════════════════════════
# Guarded operations (spec §7 — op-logged, confirm / 202 + flag)
# ═══════════════════════════════════════════════════════════════════════
def _require_confirm(body):
    if not _truthy(body.get("confirm")):
        raise ApiError(
            CONFIRMATION_REQUIRED, "this operation requires confirm:true", 409
        )


# ── Destructive (confirm required, spec §7.2) ──
@api_v1.route("/scoreboards/<int:n>:clear", methods=["POST"])
@write_op("clear")
def clear(n):
    require_board_12(n)
    _require_confirm(_json_body())
    WebOps.clear_all(n)
    return ok(revision=current_revision())


@api_v1.route("/scoreboards/<int:n>:reset-players", methods=["POST"])
@write_op("reset-players")
def reset_players(n):
    require_board_12(n)
    _require_confirm(_json_body())
    WebOps.reset_players(n)
    return ok(revision=current_revision())


@api_v1.route("/scoreboards/<int:n>:reset-match", methods=["POST"])
@write_op("reset-match")
def reset_match(n):
    require_board_12(n)
    _require_confirm(_json_body())
    WebOps.reset_match(n)
    return ok(revision=current_revision())


# ── Board overwrite (confirm recommended, spec §7.1) ──
@api_v1.route("/scoreboards/<int:n>:load-set", methods=["POST"])
@write_op("load-set")
def load_set(n):
    require_board_12(n)
    body = _json_body()
    set_id = body.get("setId")
    if set_id is None:
        raise ApiError(INVALID_ARGUMENT, "setId is required", 400)
    no_mains = _truthy(body.get("noMains", False))
    _actions_ref().load_set(n, str(set_id), no_mains=no_mains)  # @gui_thread_sync
    return ok(revision=current_revision())


@api_v1.route("/scoreboards/<int:n>:pull-stream", methods=["POST"])
@write_op("pull-stream")
def pull_stream(n):
    require_board_12(n)
    _actions_ref().pull_stream_set(n)
    return ok(revision=current_revision())


# ── Heavy operations (202 + in-progress flag, spec §2.8 / §7.3) ──
@api_v1.route("/tournament", methods=["POST"])
@write_op("tournament.load")
def tournament_load():
    body = _json_body()
    url = body.get("url", "") or ""
    if url and not _STARTGG_RE.search(url):
        raise ApiError(
            INVALID_ARGUMENT, "url must be a start.gg tournament event URL", 400
        )
    if not _heavy.try_begin("tournament"):
        raise ApiError(CONFLICT, "tournament load already in progress", 409)

    def _run():
        # load_tournament normalizes the URL and emits tournament_url_update.
        # Run on the GUI thread, that emit is a direct call into
        # AutonaTournamentDataProvider.SetTournamentSignal, so when it returns
        # the provider switch is done (the start.gg fetches it starts are
        # async and reported over WS) -- or it returned early because that
        # URL is already the loaded one. The flag is released here in both
        # cases. It used to wait for tournament_changed, which that early
        # return never emits, so re-sending the loaded URL held the flag for
        # the guard's full TTL and every retry got 409. Empty url clears the
        # tournament.
        try:
            WebOps.load_tournament(url if url else None)
        except Exception:
            logger.error(traceback.format_exc())
        finally:
            _heavy.end("tournament")

    try:
        # Fire-and-forget on the GUI thread so the request returns 202.
        AutonaQtHelper.gui_executor(_run)
    except Exception:
        _heavy.end("tournament")
        raise
    return ok(data={"accepted": True, "operation": "tournament.load"}, status=202)


@api_v1.route("/game", methods=["POST"])
@write_op("game.set")
def game_set():
    body = _json_body()
    codename = body.get("codename")
    if not codename or not isinstance(codename, str):
        raise ApiError(INVALID_ARGUMENT, "codename is required", 400)
    mods_active = _truthy(body.get("modsActive", False))
    if codename not in GameAssetCatalog.instance.GameCodenames():
        raise ApiError(NOT_FOUND, f"unknown game codename: {codename}", 404)
    if not _heavy.try_begin("game"):
        raise ApiError(CONFLICT, "game load already in progress", 409)

    def _release():
        _heavy.end("game")

    def _run():
        # Loaded by codename, not by its position in the scan: the position
        # was read here and the load ran later, so a rescan in between could
        # load another game. `done` releases the flag once the attempt is
        # over whichever way it went -- loaded, skipped by the catalog (the
        # codename was dropped by a rescan meanwhile) or failed. It used to
        # wait for onLoad, which only a load that happened emits, so the
        # other two held it for the guard's full TTL (and a UI-started load
        # finishing meanwhile released it early).
        try:
            # The shell's loader: the asset adapter on the desktop, the
            # catalog-only loader headless (ADR-005 S3c).
            WebOps.load_game(codename, mods_active=mods_active, done=_release)
        except Exception:
            logger.error(traceback.format_exc())
            _release()

    try:
        # The sync set_game action blocks on a synchronous asset reload
        # (@gui_thread_sync + async_mode=False). For 202 we start the async
        # load path instead. The worker QThread must be created on the GUI
        # thread (its parent lives there), so dispatch fire-and-forget via the
        # GUI executor (spec §14.3).
        AutonaQtHelper.gui_executor(_run)
    except Exception:
        _heavy.end("game")
        raise
    return ok(data={"accepted": True, "operation": "game.set"}, status=202)


@api_v1.route("/bracket:refresh", methods=["POST"])
@write_op("bracket.refresh")
def bracket_refresh():
    # The Top 8 import is core's (Top8Service, ADR-005 S3e-8), so this works
    # in either shell. Its in-progress flag is the import's own, held until
    # the import is over (fetches, build and write, on the provider's
    # worker) and shared with every other way in -- the panel, the hotkey,
    # the legacy route. It used to be this route's, released as soon as the
    # panel had been asked to fetch.
    if AutonaTournamentDataProvider.instance.GetProvider() is None:
        raise ApiError(NOT_READY, "no tournament loaded", 409)
    started = WebOps.update_bracket()
    if started == Top8Service.BUSY:
        raise ApiError(CONFLICT, "bracket refresh already in progress", 409)
    if started == Top8Service.NO_TOURNAMENT:
        # Cleared between the check above and the start.
        raise ApiError(NOT_READY, "no tournament loaded", 409)
    return ok(data={"accepted": True, "operation": "bracket.refresh"}, status=202)
