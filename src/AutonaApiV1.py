"""External Control API (`/api/v1/`).

A thin, structured HTTP namespace added to the existing Flask + SocketIO
server (ADR-001 D3). Every handler delegates to the existing
``WebServerActions`` so state logic is never duplicated; state always flows
``WebServerActions`` -> Qt signal -> ``StateManager`` (D4). Existing routes
(``/score``, ``/scoreboard<N>-...``) and Socket.IO events are left untouched.

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

from .StateManager import StateManager
from .SettingsManager import SettingsManager
# Import order mirrors the singleton init order (agent-pitfalls #2): the asset
# manager must be importable before the tournament data provider. By the time
# this module loads both singletons already exist (WebServerActions imported
# them first), so this is only defensive.
from .AutonaGameAssetManager import AutonaGameAssetManager
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .Helpers import AutonaQtHelper

# ── Blueprint ────────────────────────────────────────────────────────────
api_v1 = Blueprint("api_v1", __name__, url_prefix="/api/v1")

# Dedicated logger for write operations (spec §2.7 / D7 A-1). Reads are not
# logged here. Bound so the channel can be filtered downstream.
_oplog = logger.bind(channel="api_v1_ops")

# Reference to the live WebServerActions instance, injected at registration
# time so this module never imports AutonaWebServer (avoids a circular import).
_actions = None

_version_cache = None


# ── Error model (spec §2.6) ──────────────────────────────────────────────
INVALID_ARGUMENT = "INVALID_ARGUMENT"        # 400
NOT_FOUND = "NOT_FOUND"                       # 404
CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"  # 409
CONFLICT = "CONFLICT"                         # 409
NOT_READY = "NOT_READY"                       # 409
UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"         # 504
INTERNAL = "INTERNAL"                         # 500


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
    """Parse the JSON request body as an object (or {} when absent)."""
    data = request.get_json(silent=True)
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
    if _actions is None:
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
        except Exception:
            logger.error(traceback.format_exc())
            return err(INTERNAL, "internal error", 500)

    return wrapper


def _safe_params():
    # In-scope write bodies carry only small scalar params (no PII / secrets;
    # set_team_data is out of scope). Truncate defensively all the same.
    body = request.get_json(silent=True)
    if not body:
        return {}
    text = repr(body)
    return text if len(text) <= 300 else text[:300] + "…"


def write_op(operation):
    """Write endpoints: exception->envelope conversion plus an INFO op-log
    line recording IP / method / path / params / result (spec §2.7)."""

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            params = _safe_params()
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

    The flag is held until a completion signal clears it (see
    _connect_completion_signals) and auto-expires after ``ttl`` as a safety
    net so a missed completion never sticks the flag forever. Completion is
    authoritatively observed by clients via WS revision progress (spec §2.8).

    ``ttl`` is a generous backstop, not the primary clear: a full game asset
    reload can take ~60-70s, so it must comfortably exceed the slowest heavy
    op or the flag would expire mid-operation and the completion signal
    (onLoad / tournament_changed) would never be the effective clear.
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
    """Bind the live WebServerActions and wire completion signals.

    Runs on the GUI thread (WebServer is constructed there), so connecting Qt
    signals here is safe.
    """
    global _actions
    _actions = actions
    _connect_completion_signals()


def _connect_completion_signals():
    # Clear heavy-op flags when the underlying operation finishes. These
    # signals also fire for UI-triggered loads; end() is a harmless no-op when
    # the flag isn't set, and loads are serialized upstream so this can't clear
    # a different in-flight API op.
    try:
        AutonaGameAssetManager.instance.signals.onLoad.connect(
            lambda: _heavy.end("game")
        )
        AutonaTournamentDataProvider.instance.signals.tournament_changed.connect(
            lambda: _heavy.end("tournament")
        )
    except Exception:
        logger.error(traceback.format_exc())


# ═══════════════════════════════════════════════════════════════════════
# Read endpoints (spec §5 — GET, not op-logged)
# ═══════════════════════════════════════════════════════════════════════
@api_v1.route("/state")
@api_handler
def state():
    full = _actions_ref().program_state()  # {'state','revision','delta_index'}
    return ok(data=full.get("state"), revision=full.get("revision"))


@api_v1.route("/health")
@api_handler
def health():
    provider = AutonaTournamentDataProvider.instance.GetProvider()
    game = StateManager.Get("game") or {}
    current_game = game.get("codename") if isinstance(game, dict) else None
    fmt = SettingsManager.Get("scoreboard.battle_format", "singles")
    active_board = {"singles": 1, "team": 2, "crew": 3}.get(fmt, 1)
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
    actions = _actions_ref()
    board_state = actions.get_scoreboard(n)
    data = dict(board_state) if isinstance(board_state, dict) else {"state": board_state}
    # teamsSwapped lives on the widget; crew board may lack it — tolerate.
    try:
        data["swapped"] = actions.get_swap(n) == "True"
    except Exception:
        pass
    return ok(data=data, revision=current_revision())


@api_v1.route("/games")
@api_handler
def get_games():
    return ok(data=_actions_ref().get_games())


@api_v1.route("/games/current")
@api_handler
def get_current_game():
    return ok(data=_actions_ref().get_current_game())


@api_v1.route("/match-names")
@api_handler
def get_match_names():
    return ok(data=_actions_ref().get_match_names())


@api_v1.route("/characters")
@api_handler
def get_characters():
    return ok(data=_actions_ref().get_characters())


@api_v1.route("/variants")
@api_handler
def get_variants():
    return ok(data=_actions_ref().get_variants())


@api_v1.route("/controllers")
@api_handler
def get_controllers():
    return ok(data=_actions_ref().get_controllers())


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
        return ok(data=_actions_ref().get_sets(args))
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
        return ok(data=_actions_ref().get_match(sid))
    except ApiError:
        raise
    except Exception:
        logger.error(traceback.format_exc())
        raise ApiError(UPSTREAM_TIMEOUT, "start.gg request failed", 504)


@api_v1.route("/commentary")
@api_handler
def get_commentary():
    return ok(data=_actions_ref().get_comms())


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
    actions = _actions_ref()

    def apply():
        # Non-sync: emits CommandScoreChange |delta| times (spec §6.1).
        if delta >= 0:
            for _ in range(delta):
                actions.team_scoreup(n, t)
        else:
            for _ in range(-delta):
                actions.team_scoredown(n, t)

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
    # Non-sync emit: revision is best-effort; client confirms via WS (§14.1a).
    return ok(revision=current_revision())


@api_v1.route("/scoreboards/<int:n>/score:reset", methods=["POST"])
@write_op("score.reset")
def score_reset(n):
    require_board_12(n)
    _actions_ref().reset_scores(n)  # @gui_thread_sync -> revision accurate
    return ok(revision=current_revision())


@api_v1.route("/scoreboards/<int:n>:swap-teams", methods=["POST"])
@write_op("swap-teams")
def swap_teams(n):
    require_board_12(n)
    _actions_ref().swap_teams(n)
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
    _actions_ref().team_color(n, t, color)
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
    if "match" in body:
        kwargs["match"] = body["match"]
    if "players" in body:
        kwargs["players"] = _require_int(body["players"], "players")
    if "characters" in body:
        kwargs["characters"] = _require_int(body["characters"], "characters")
    if "losers" in body:
        losers = body["losers"]
        if not isinstance(losers, dict):
            raise ApiError(INVALID_ARGUMENT, "losers must be an object {team,value}", 400)
        team = _validate_team(losers.get("team"))
        # set_route expects a string for losers (it calls .lower()).
        kwargs["losers"] = "true" if _truthy(losers.get("value")) else "false"
        kwargs["team"] = team
    _actions_ref().set_route(n, **kwargs)  # @gui_thread_sync -> revision accurate
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
    _actions_ref().clear_all(n)  # @gui_thread_sync
    return ok(revision=current_revision())


@api_v1.route("/scoreboards/<int:n>:reset-players", methods=["POST"])
@write_op("reset-players")
def reset_players(n):
    require_board_12(n)
    _require_confirm(_json_body())
    _actions_ref().reset_players(n)
    return ok(revision=current_revision())


@api_v1.route("/scoreboards/<int:n>:reset-match", methods=["POST"])
@write_op("reset-match")
def reset_match(n):
    require_board_12(n)
    _require_confirm(_json_body())
    _actions_ref().reset_match(n)
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


@api_v1.route("/scoreboards/<int:n>:pull-user", methods=["POST"])
@write_op("pull-user")
def pull_user(n):
    require_board_12(n)
    _actions_ref().pull_user_set(n)
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
    try:
        # load_tournament emits a queued signal and returns immediately; the
        # actual fetch runs async. Empty url clears the tournament.
        _actions_ref().load_tournament(url if url else None)
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
    games = AutonaGameAssetManager.instance.games
    if codename not in games:
        raise ApiError(NOT_FOUND, f"unknown game codename: {codename}", 404)
    index = list(games.keys()).index(codename)  # 0-based; LoadGameAssets is 1-based
    if not _heavy.try_begin("game"):
        raise ApiError(CONFLICT, "game load already in progress", 409)
    try:
        # The sync set_game action blocks on a synchronous asset reload
        # (@gui_thread_sync + async_mode=False). For 202 we start the async
        # load path instead. The worker QThread must be created on the GUI
        # thread (its parent lives there), so dispatch fire-and-forget via the
        # GUI executor (spec §14.3). Completion clears the flag via onLoad.
        AutonaQtHelper.gui_executor(
            lambda: AutonaGameAssetManager.instance.LoadGameAssets(
                index + 1,
                async_mode=True,
                mods_active=mods_active,
                mods_reload_mode=True,
            )
        )
    except Exception:
        _heavy.end("game")
        raise
    return ok(data={"accepted": True, "operation": "game.set"}, status=202)


@api_v1.route("/bracket:refresh", methods=["POST"])
@write_op("bracket.refresh")
def bracket_refresh():
    provider = AutonaTournamentDataProvider.instance.GetProvider()
    if provider is None:
        raise ApiError(NOT_READY, "no tournament loaded", 409)
    if not _heavy.try_begin("bracket"):
        raise ApiError(CONFLICT, "bracket refresh already in progress", 409)

    def _run():
        # update_bracket is @gui_thread_sync with weak guards and a synchronous
        # start.gg call. Wrap (don't modify the existing action) so failures
        # are logged and the flag is always released (spec §14.2 / §14.3).
        try:
            _actions_ref().update_bracket()
        except Exception:
            logger.error(traceback.format_exc())
        finally:
            _heavy.end("bracket")

    try:
        # Fire-and-forget on the GUI thread so the HTTP request returns 202.
        AutonaQtHelper.gui_executor(_run)
    except Exception:
        _heavy.end("bracket")
        raise
    return ok(data={"accepted": True, "operation": "bracket.refresh"}, status=202)
