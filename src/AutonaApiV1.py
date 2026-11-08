"""External Control API (`/api/v1/`).

A thin, structured HTTP namespace added to the existing Flask + SocketIO
server (ADR-001 D3). Every handler delegates to the operations the legacy
routes use, so state logic is never duplicated: ``AutonaWebOperations``,
the same in both shells (ADR-005 S3c; every operation since S3e-9 -- none
of the two shell operations, which only the desktop registers, is a v1
one). Existing routes (``/score``, ``/scoreboard<N>-...``) and Socket.IO
events are left untouched.

Spec (source of truth): ``.claude/design/external-control-api.md``.

Sections below mirror the implementation tickets:
  - foundation + read endpoints  (docs/tasks/implement-external-control-api-readonly.md)
  - safe write operations        (docs/tasks/implement-external-control-api-safe-ops.md)
  - guarded operations           (docs/tasks/implement-external-control-api-guarded-ops.md)
"""

import base64
import binascii
import functools
import json
import os
import re
import threading
import time
import traceback
from collections import OrderedDict

from flask import Blueprint, Response, jsonify, request
from loguru import logger
from werkzeug.exceptions import ClientDisconnected

from .core.StateManager import StateManager
from .core.Actions import AutonaActions
from .core import AnalysisService as analysis
from .core import StateSchema as schema
from .core import ColorService
from .core import ImageImport
from .core import CommentaryModel as comms
from .core import CommentaryService
from .core import LayoutCatalog
from .core import PlayerDirectory
from .core import ResultService as results
from .core import StreamService as streams
from .core import ThumbnailService as thumbnails
from .core import Top8Model as top8
from .core import Top8Service
from .core import TournamentMediaService as tournamentMedia
from .core import TournamentService as tournaments
from .core.GameAssetCatalog import GameAssetCatalog
from .core.TournamentInfoService import TournamentInfoService
from .core.CountryCatalog import CountryCatalog
# Importing these builds nothing (agent-pitfalls #1): their singletons
# (`.instance`) are created by src/bootstrap.py and read per request.
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .Helpers import AutonaQtHelper
from . import AutonaWebOperations as WebOps
from .AutonaWebOperations import NotAvailable

# ── Blueprint ────────────────────────────────────────────────────────────
api_v1 = Blueprint("api_v1", __name__, url_prefix="/api/v1")

# Dedicated logger for write operations (spec §2.7 / D7 A-1). Reads are not
# logged here. Bound so the channel can be filtered downstream.
_oplog = logger.bind(channel="api_v1_ops")

_version_cache = None


# ── Error model (spec §2.6) ──────────────────────────────────────────────
INVALID_ARGUMENT = "INVALID_ARGUMENT"        # 400
NOT_FOUND = "NOT_FOUND"                       # 404
CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"  # 409
CONFLICT = "CONFLICT"                         # 409
NOT_READY = "NOT_READY"                       # 409
UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"         # 504
INTERNAL = "INTERNAL"                         # 500
# A request body over its route's limit (write_op's max_body: the image
# uploads, the stream org's (ADR-005 S4e) and the tournament's (S4g)).
PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"       # 413
# An operation this shell cannot run (ADR-005 S3c): the server runs, the
# operation needs what the shell does not have. Not retryable. In v1, only
# starting a match thumbnail's render does (ADR-005 S4o): the off-screen view
# is the desktop's.
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


def _color_rrggbb(color):
    """A colour `_COLOR_RE` took (#RGB / #RRGGBB, either case) as the
    settings and the state keep one: "#rrggbb", what the Qt colour dialog
    saved (QColor.name()) and the overlays and the pages' colour inputs
    read (S4g review L13)."""
    digits = color[1:]
    if len(digits) == 3:
        digits = "".join(digit * 2 for digit in digits)
    return "#" + digits.lower()


def current_revision():
    """Best-effort current state revision (spec §14.1 / §14.1a)."""
    return StateManager.revision


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


# A refused body is read in pieces of this size and dropped.
_DRAIN_CHUNK = 1024 * 1024


def _over_limit(max_body):
    """Whether the request's Content-Length is over `max_body`; if it is,
    the body is read to its end and dropped first.

    Answering without reading it would leave the body unread on the socket:
    werkzeug's server closes the connection after the answer, and a client
    still sending then sees a connection reset instead of the 413 (the
    server's own drain gives up after 10 ms of silence). werkzeug drains as
    much on its own, so this costs no more than its policy, only reliably.
    A body without a Content-Length (chunked) is read as on every other
    route; the handler's own checks still refuse what is too large."""
    length = request.content_length
    if length is None or length <= max_body:
        return False
    stream = request.stream
    try:
        while stream.read(_DRAIN_CHUNK):
            pass
    except (ClientDisconnected, OSError):
        # Gone before the end (or the body is shorter than it said): no
        # one is left to read the answer, or all of it was read.
        pass
    return True


def write_op(operation, log_keys_only=False, max_body=None):
    """Write endpoints: exception->envelope conversion plus an INFO op-log
    line recording IP / method / path / params / result (spec §2.7).

    ``max_body``: this route's request body limit in bytes, answered 413
    PAYLOAD_TOO_LARGE when the Content-Length is over it, the body dropped
    rather than parsed (_over_limit). The app sets no limit of its own (no
    MAX_CONTENT_LENGTH), so the other routes are as they were."""

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            # Before anything reads the body: _safe_params below does (and
            # caches it for the handler's read).
            if max_body is not None and _over_limit(max_body):
                _oplog.info(
                    f"{request.remote_addr} {request.method} {request.path} "
                    f"op={operation} params=<over {max_body} bytes> -> 413 "
                    f"{PAYLOAD_TOO_LARGE}"
                )
                return err(PAYLOAD_TOO_LARGE,
                           f"the request body is over {max_body} bytes", 413)
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

# No initialization step: WebServer.__init__ registers the blueprint and
# nothing else. The handlers call AutonaWebOperations, which the shell has
# filled in before the server starts (bootstrap_gui() / src/headless.py),
# and every heavy op releases its in-progress flag itself (_HeavyOpGuard).
# init_api_v1(actions) -- the desktop's actions object and the 409
# NOT_READY "API not initialized" it answered before it was bound -- went
# with that object in ADR-005 S3e-9: no route used it since S3e-7, and no
# request could reach the blueprint before it was bound.


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


@api_v1.route("/countries")
@api_handler
def get_countries():
    # The country combo's rows (ADR-005 S4i; the Top 8 page picks from
    # them): every country in the program's locale, then the custom flags
    # (user_data/additional_flag), each with its `code` -- what PATCH
    # /top8/players/<slot> takes -- its flag `asset`, its `label` and
    # whether it is `custom`. Not state, so no revision. NOT_READY until
    # the country data is loaded, as /countries/<code>/states.
    if not CountryCatalog.instance.IsLoaded():
        raise ApiError(NOT_READY, "country data is not loaded yet", 409)
    return ok(data=WebOps.get_countries())


@api_v1.route("/layouts")
@api_handler
def get_layouts():
    # The layout catalog the Board tab, the Commentary / Top 8 pages and the Designer
    # list from (src/core/LayoutCatalog.py). Not state, so no revision and
    # no web-operation hop: the catalog is already the single reader.
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


@api_v1.route("/commentary/options")
@api_handler
def get_commentary_options():
    # The role candidates and the icons the Commentary page offers (ADR-005
    # S4b), from core's constants. Not state, so no revision.
    return ok(data=WebOps.get_commentary_options())


def _selection_kind(kind):
    if kind not in LayoutCatalog.SELECTION_SETTINGS:
        raise ApiError(
            INVALID_ARGUMENT,
            f"no layout selection for kind: {kind} (expected one of: "
            f"{', '.join(LayoutCatalog.SELECTION_SETTINGS)})",
            400,
        )
    return kind


@api_v1.route("/layouts/selected/<kind>")
@api_handler
def get_selected_layout(kind):
    # The design a panel shows and hands to OBS (settings.json, the panel's
    # own key; ADR-005 S4b): the catalog entry, with the Qt panel's
    # fallbacks when the kept one is gone; 404 when the kind lists none.
    entry = WebOps.get_selected_layout(_selection_kind(kind))
    if entry is None:
        raise ApiError(NOT_FOUND, f"no {kind} layout is installed", 404)
    return ok(data=entry)


@api_v1.route("/stream/channels")
@api_handler
def get_stream_channels():
    # The saved stream channels, each {"name", "url"} (settings.json's
    # stream_channels; ADR-005 S4e). Settings, not state: no revision.
    return ok(data={"channels": WebOps.get_stream_channels()})


@api_v1.route("/stream/thumbnail/options")
@api_handler
def get_stream_thumbnail_options():
    # The stream thumbnail generator's saved options (settings.json's
    # stream_thumbnail_options): {"source", "style", "overlayOrgIcon"}.
    return ok(data=WebOps.get_stream_thumbnail_options())


@api_v1.route("/thumbnail")
@api_handler
def get_thumbnail():
    # The match thumbnail's page (ADR-005 S4o): {"settings", "game": {codename,
    # name} | null, "templates": [{id, name}], "videoTitle", "canRender"}.
    # thumbnail_config with its defaults filled in; nothing is written.
    # Settings, not state: no revision.
    return ok(data=WebOps.describe_thumbnail())


@api_v1.route("/thumbnail/preview")
@api_handler
def get_thumbnail_preview():
    # The newest render as a PNG (POST /thumbnail:preview | :generate). A file
    # of ./tmp, which the web server's static directories do not include. A
    # client asks ?v=<thumbnail.render.revision>, which is not looked at: it
    # only makes the URL new. Bytes, not an envelope; 404 NOT_FOUND until the
    # first render.
    image = WebOps.get_thumbnail_preview()
    if image is None:
        raise ApiError(NOT_FOUND, "no thumbnail preview has been drawn", 404)
    return Response(image, mimetype="image/png",
                    headers={"Cache-Control": "no-store"})


@api_v1.route("/result")
@api_handler
def get_result():
    # The tournament result's page (ADR-005 S4q): {"layout", "layouts": [{id,
    # name, width, height, slots, team}], "players": sixteen rows {slot, rank,
    # name, sponsor, twitter, characters: three of {en_name, skin} | null,
    # fromScoreboard: the name of the character drawn for a row without one |
    # null}, "teams": eight rows {slot, rank, name, members: [{name, twitter,
    # character, fromScoreboard}]}, "sample": whether a preview now draws the
    # sample, "game", "render", "standings", "canRender"}. Nothing is written.
    return ok(data=WebOps.describe_result(), revision=current_revision())


@api_v1.route("/result/post")
@api_handler
def get_result_post():
    # The X post's text, {"text"}. Guarded like the player DB, which its handles
    # come from (PLAYERDB), and not part of GET /result, which anyone on the
    # network may read. A page holds it and reads it in the click that opens the
    # X window: a browser opens one only while handling a click. Nothing is
    # written.
    return ok(data={"text": WebOps.get_result_post()},
              revision=current_revision())


@api_v1.route("/result/preview")
@api_handler
def get_result_preview():
    # The newest render as a PNG, as GET /thumbnail/preview: a client asks
    # ?v=<result_render.revision>. Bytes, not an envelope; 404 NOT_FOUND until
    # the first render.
    image = WebOps.get_result_preview()
    if image is None:
        raise ApiError(NOT_FOUND, "no result preview has been drawn", 404)
    return Response(image, mimetype="image/png",
                    headers={"Cache-Control": "no-store"})


@api_v1.route("/tournament")
@api_handler
def get_tournament():
    # The tournament URL last set (settings.json's TOURNAMENT_URL), whether
    # a provider is there (`loaded`: also after a load that failed), and
    # how the newest set / clear is going -- status
    # idle | resolving | loading | loaded | error, its error (unresolved |
    # load_failed) and the revision every set / clear advances: the state's
    # tournamentInfo.url / load (ADR-005 S4g), which the Event page renders
    # without asking here.
    return ok(data=WebOps.get_tournament(), revision=current_revision())


@api_v1.route("/analysis")
@api_handler
def get_analysis():
    # The tournament's match list (ADR-005 S4k; core's AnalysisService):
    # `status` is the state's analysis.status, `rows` the newest fetch's that
    # made them, with the `url` of their tournament -- none ("" and []) unless
    # that is the tournament loaded now. Each row is a structure (round and
    # its sort key, both sides' seed and name, score, upset, losersRun,
    # seedOver); the wording is the reader's. The rows are not in the state
    # (hundreds to over a thousand), so the Statistics page reads them here
    # whenever analysis.revision / status changes.
    return ok(data=WebOps.get_analysis(), revision=current_revision())


@api_v1.route("/players")
@api_handler
def get_players():
    # Every player of the local database, in the order of their keys, with
    # the loaded game's mains and that game's codename (ADR-005 S4m; core's
    # PlayerDirectory): PUT /players takes `game` back with the mains. The
    # database, not state: no revision. Real names in it, so WebAccess has
    # it as PLAYERDB, as /playerdb.
    return ok(data=WebOps.get_players())


@api_v1.route("/players/entrants")
@api_handler
def get_player_entrants():
    # The loaded tournament's entrants, best seed first, each with `seed` and
    # the database's record for its tag; one it does not have is the tag
    # alone, `key` null. [] with no tournament. `game` and PLAYERDB, as
    # /players.
    return ok(data=WebOps.get_player_entrants())


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
    # Board 3 (crew) has no statistics, so it is rejected like every other
    # board1/2-only operation rather than silently doing nothing.
    require_board_12(n)
    # Core's (StatsService, ADR-005 S3e-5), in both shells. The start.gg
    # fetches it kicks off are async: the revision here only covers the
    # synchronous part (spec §14.1a) — clients observe the finished stats
    # via the WS delta stream.
    WebOps.refresh_stats(n)
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


def _is_main_entry(entry, min_skin=0):
    """[name: str, skin: int >= min_skin, variant?: str]"""
    return (isinstance(entry, list) and 2 <= len(entry) <= 3
            and isinstance(entry[0], str)
            and isinstance(entry[1], int)
            and not isinstance(entry[1], bool)
            and entry[1] >= min_skin
            and (len(entry) == 2 or isinstance(entry[2], str)))


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
            if not _is_main_entry(entry):
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


@api_v1.route(
    "/scoreboards/<int:n>/teams/<int:t>/players/<int:p>:load", methods=["POST"])
@write_op("player.load", log_keys_only=True)
def player_load(n, t, p):
    # {"tag"}: load the local database's player of that gamer tag (ignoring
    # case; no sponsor) into the slot, clearing it first (ADR-005 S4m; the
    # legacy -load-player-from-tag- route's operation, and the player list's
    # "set to P1 / P2"). Unlike PATCH players, `t` is the set's team: after
    # a swap team 1 lands on state team 2, as the legacy route's. An unknown
    # tag or a slot the team does not have: 404, with no tag in the message.
    require_board_12(n)
    _validate_team(t)
    if p < 1:
        raise ApiError(INVALID_ARGUMENT, "player must be 1 or greater", 400)
    tag = _json_body().get("tag")
    if not isinstance(tag, str) or not tag:
        raise ApiError(INVALID_ARGUMENT, "tag must be a non-empty string", 400)
    try:
        result = WebOps.load_player_from_tag(n, tag, t, p)
    except WebOps.NoSuchSlot:
        raise ApiError(NOT_FOUND, f"team {t} has no player {p}", 404)
    if result != "OK":
        raise ApiError(NOT_FOUND, "no player with that tag", 404)
    return ok(revision=current_revision())


# What PUT /players answers for each of PlayerDirectory.Save's refusals. Fixed
# messages: a refused value can be a person's.
_PLAYER_SAVE_REFUSALS = {
    PlayerDirectory.NO_TAG:
        (INVALID_ARGUMENT, "gamerTag is required", 400),
    PlayerDirectory.UNKNOWN_COUNTRY:
        (INVALID_ARGUMENT, "unknown country_code", 400),
    PlayerDirectory.UNKNOWN_STATE:
        (INVALID_ARGUMENT, "state_code is not a region of country_code", 400),
    PlayerDirectory.UNKNOWN_CHARACTER:
        (INVALID_ARGUMENT, "mains names a character the loaded game does "
                           "not have", 400),
    PlayerDirectory.UNKNOWN_SKIN:
        (INVALID_ARGUMENT, "mains names a skin its character does not have",
         400),
    PlayerDirectory.UNKNOWN_VARIANT:
        (INVALID_ARGUMENT, "mains names a variant the loaded game does not "
                           "have", 400),
    PlayerDirectory.OTHER_GAME:
        (CONFLICT, "the mains are not of the loaded game (another one was "
                   "named, or none is loaded)", 409),
    PlayerDirectory.SUPERSEDED:
        (CONFLICT, "a newer save of this player came first; nothing was "
                   "saved", 409),
}

# PUT /players' texts. Far over any real value: they keep what a client sends
# out of the file and of every row that reads it, not a name out of the file.
_PLAYER_LINE_MAX = 256
_PLAYER_NOTE_MAX = 4096


def _player_save_text(key, value):
    """A text of PUT /players as it is saved. The note has lines. The others
    are one line, trimmed: a space pasted around a tag would be another
    database key. No value in the messages: a refused one can be a person's."""
    if key == "custom_textbox":
        if len(value) > _PLAYER_NOTE_MAX:
            raise ApiError(
                INVALID_ARGUMENT,
                f"{key} must be at most {_PLAYER_NOTE_MAX} characters", 400)
        return value
    value = value.strip()
    if len(value.splitlines()) > 1:
        raise ApiError(INVALID_ARGUMENT, f"{key} must be one line", 400)
    if len(value) > _PLAYER_LINE_MAX:
        raise ApiError(
            INVALID_ARGUMENT,
            f"{key} must be at most {_PLAYER_LINE_MAX} characters", 400)
    return value


@api_v1.route("/players", methods=["PUT"])
@write_op("player.save", log_keys_only=True)
def player_save():
    # Save a player into the local database (ADR-005 S4m; core's
    # PlayerDirectory.Save, the state untouched). The texts of PATCH players;
    # a text that is missing is empty, but country_code / state_code stay as
    # stored unless sent ("" clears). `mains`, a list of [en_name, skin,
    # variant?], replace the loaded game's only, and come with the `game` they
    # were picked for (GET /players' `game`, null for none); without them no
    # game's change. An entry the player already has goes unchecked: -1 is a
    # skin here for that. Answers the database key and whether it is a new one.
    body = _json_body()
    fields = {}
    for key, value in body.items():
        if key in _PLAYER_STRING_FIELDS or key == "game":
            if value is not None and not isinstance(value, str):
                raise ApiError(INVALID_ARGUMENT, f"{key} must be a string", 400)
            if value is not None and key != "game":
                value = _player_save_text(key, value)
        elif key == "mains":
            if not isinstance(value, list) \
                    or len(value) > AutonaActions.MAX_CHARACTERS_PER_PLAYER \
                    or not all(_is_main_entry(entry, min_skin=-1)
                               for entry in value):
                raise ApiError(
                    INVALID_ARGUMENT,
                    "mains must be at most "
                    f"{AutonaActions.MAX_CHARACTERS_PER_PLAYER} "
                    "[name: str, skin: int >= -1, variant?: str]", 400)
        else:
            raise ApiError(INVALID_ARGUMENT, f"unknown player field: {key}", 400)
        fields[key] = value
    if "mains" in fields and "game" not in fields:
        raise ApiError(
            INVALID_ARGUMENT, "game is required with mains (GET /players has "
                              "it, null with no game)", 400)
    if (fields.get("country_code") or fields.get("state_code")) \
            and not CountryCatalog.instance.IsLoaded():
        raise ApiError(NOT_READY, "country data is not loaded yet", 409)
    result = WebOps.save_player(fields)
    if isinstance(result, str):
        raise ApiError(*_PLAYER_SAVE_REFUSALS[result])
    return ok(data=result)


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


# ── the Commentary page (ADR-005 S4b) ──
# The writes the Commentary page asks core for, one route each:
# CommentaryService.SetField / Reorder / ApplyPlayerData. The line-up is
# not held, so each is published when it returns. Names and handles are
# personal data: logged by key only.
def _caster_slot(slot):
    if str(slot) not in comms.SLOTS:
        raise ApiError(
            INVALID_ARGUMENT, f"slot must be 1..{len(comms.SLOTS)}", 400)
    return str(slot)


@api_v1.route("/commentary/<int:slot>", methods=["PATCH"])
@write_op("commentary.update", log_keys_only=True)
def commentary_update(slot):
    slot = _caster_slot(slot)
    body = _json_body()
    if not body:
        raise ApiError(
            INVALID_ARGUMENT, "at least one caster field is required", 400)
    # Validate every field before writing any, so a bad one changes nothing.
    fields = comms.DefaultSlot()
    unknown = sorted(k for k in body if k not in fields)
    if unknown:
        raise ApiError(
            INVALID_ARGUMENT, f"unknown caster field: {', '.join(unknown)}", 400)
    for key, value in body.items():
        if key == "display":
            if not isinstance(value, bool):
                raise ApiError(
                    INVALID_ARGUMENT, "display must be true or false", 400)
        elif not isinstance(value, str):
            raise ApiError(INVALID_ARGUMENT, f"{key} must be a string", 400)
        elif key == "icon" and value and value not in comms.ICONS:
            raise ApiError(
                INVALID_ARGUMENT,
                f"unknown icon: {value} (expected an empty string or one "
                f"of: {', '.join(comms.ICONS)})",
                400,
            )
    # Showing a caster past MAX_DISPLAY others is refused, as core does;
    # the other fields of the same request are not written either.
    if not WebOps.update_caster(slot, body):
        raise ApiError(
            CONFLICT,
            f"at most {comms.MAX_DISPLAY} casters can be shown at once", 409)
    return ok(revision=current_revision())


@api_v1.route("/commentary/reorder", methods=["POST"])
@write_op("commentary.reorder")
def commentary_reorder():
    # {"order": [...]}: for slot 1, 2, ... in turn, the slot whose caster
    # goes there -- every slot once (CommentaryService.Reorder).
    order = _json_body().get("order")
    if not isinstance(order, list) or any(
            isinstance(slot, bool) or not isinstance(slot, (str, int))
            for slot in order):
        raise ApiError(
            INVALID_ARGUMENT, "order must be a list of slot numbers", 400)
    order = [str(slot) for slot in order]
    if sorted(order) != sorted(comms.SLOTS):
        raise ApiError(
            INVALID_ARGUMENT,
            f"order must name every slot 1..{len(comms.SLOTS)} once", 400)
    WebOps.reorder_casters(order)
    return ok(revision=current_revision())


# The keys of a picked player that fill a caster (CommentaryService).
_CASTER_PLAYER_KEYS = [
    source for _field, source in CommentaryService.PLAYERDB_FIELD_MAP]


@api_v1.route("/commentary/<int:slot>/player", methods=["POST"])
@write_op("commentary.player", log_keys_only=True)
def commentary_player(slot):
    # A player picked from the player DB (the name completer): an object
    # shaped like the Socket.IO `playerdb` push's players. Its gamerTag /
    # prefix / twitter / custom_textbox fill the caster's name / team /
    # twitter / note (censored); other keys are ignored, and role, icon and
    # display are left alone.
    slot = _caster_slot(slot)
    body = _json_body()
    for key in _CASTER_PLAYER_KEYS:
        if body.get(key) is not None and not isinstance(body[key], str):
            raise ApiError(INVALID_ARGUMENT, f"{key} must be a string", 400)
    if not any(isinstance(body.get(key), str) for key in _CASTER_PLAYER_KEYS):
        raise ApiError(
            INVALID_ARGUMENT,
            f"a player needs one of: {', '.join(_CASTER_PLAYER_KEYS)}", 400)
    WebOps.apply_caster_player(slot, body)
    return ok(revision=current_revision())


@api_v1.route("/layouts/selected/<kind>", methods=["PUT"])
@write_op("layout.select")
def select_layout(kind):
    # {"id": "<layout id>"}: one the catalog lists for the kind. Kept under
    # the panel's own settings.json key, as the panel keeps it. Settings,
    # not state: no revision.
    kind = _selection_kind(kind)
    layoutId = _json_body().get("id")
    if not isinstance(layoutId, str) or not layoutId:
        raise ApiError(INVALID_ARGUMENT, "id must be a layout id", 400)
    entry = WebOps.select_layout(kind, layoutId)
    if entry is None:
        raise ApiError(
            INVALID_ARGUMENT, f"unknown {kind} layout: {layoutId}", 400)
    return ok(data=entry)


@api_v1.route("/stats/overlays")
@api_handler
def get_stats_overlays():
    # The statistics overlays the Stats Display Settings page offers, the
    # selected one and the Companion macro's request (ADR-005 S4l; core's
    # StatsOverlays). `refresh.url` is built here: only the server knows the
    # host (the LAN IP when LAN access is on). Settings, not state: no
    # revision.
    return ok(data=WebOps.get_stats_overlays())


@api_v1.route("/stats/overlays/selected", methods=["PUT"])
@write_op("stats.overlay.select")
def select_stats_overlay():
    # {"key": "<overlay key>"}: one GET /stats/overlays lists. Kept as the
    # Qt panel kept it (settings.json's stats_preview.selected_type).
    key = _json_body().get("key")
    item = WebOps.select_stats_overlay(key) if isinstance(key, str) else None
    if item is None:
        raise ApiError(INVALID_ARGUMENT, "key must be a listed overlay", 400)
    return ok(data=item)


@api_v1.route("/colors/<feature>", methods=["PATCH"])
@write_op("colors.update")
def colors_update(feature):
    # {"theme": "#rrggbb", "accent": "#rrggbb"}, either or both: one
    # feature's own colours (ColorService.SetFeatureColor), which the
    # overlays read before the tournament's.
    if feature not in ColorService.FEATURES:
        raise ApiError(
            INVALID_ARGUMENT,
            f"unknown feature: {feature} (expected one of: "
            f"{', '.join(ColorService.FEATURES)})",
            400,
        )
    body = _json_body()
    kinds = (ColorService.KIND_THEME, ColorService.KIND_ACCENT)
    unknown = sorted(k for k in body if k not in kinds)
    if unknown:
        raise ApiError(
            INVALID_ARGUMENT, f"unknown color: {', '.join(unknown)}", 400)
    if not body:
        raise ApiError(INVALID_ARGUMENT, "theme or accent is required", 400)
    for kind, color in body.items():
        if not isinstance(color, str) or not _COLOR_RE.match(color):
            raise ApiError(
                INVALID_ARGUMENT, f"{kind} must be #RGB or #RRGGBB", 400)
    WebOps.set_feature_colors(
        feature, {kind: _color_rrggbb(color) for kind, color in body.items()})
    return ok(revision=current_revision())


@api_v1.route("/colors/tournament", methods=["PATCH"])
@write_op("colors.tournament")
def colors_tournament_update():
    # {"theme": "#rrggbb", "accent": "#rrggbb"}, either or both (#RGB too,
    # either case: kept as #rrggbb), or {"reset": true}: Autona's own pair
    # (the Qt card's "Autonaデフォルトカラーを設定する"). The tournament's
    # colours -- settings.json's general.*_color, published as
    # tournamentInfo.themeColor / accentColor -- and each clears the
    # feature overrides of its kind (ColorService.SetTournamentColor;
    # ADR-005 S4g). Answers the pair now. A static rule, so werkzeug
    # matches it ahead of /colors/<feature>.
    body = _json_body()
    kinds = (ColorService.KIND_THEME, ColorService.KIND_ACCENT)
    if "reset" in body:
        if body.get("reset") is not True or len(body) != 1:
            raise ApiError(
                INVALID_ARGUMENT, "reset must be true, and sent alone", 400)
        colors = {ColorService.KIND_THEME: schema.DEFAULT_THEME_COLOR,
                  ColorService.KIND_ACCENT: schema.DEFAULT_ACCENT_COLOR}
    else:
        unknown = sorted(k for k in body if k not in kinds)
        if unknown:
            raise ApiError(
                INVALID_ARGUMENT, f"unknown color: {', '.join(unknown)}",
                400)
        if not body:
            raise ApiError(
                INVALID_ARGUMENT, "theme, accent or reset is required", 400)
        for kind, color in body.items():
            if not isinstance(color, str) or not _COLOR_RE.match(color):
                raise ApiError(
                    INVALID_ARGUMENT, f"{kind} must be #RGB or #RRGGBB", 400)
        colors = {kind: _color_rrggbb(color) for kind, color in body.items()}
    WebOps.set_tournament_colors(colors)
    theme, accent = ColorService.GetTournamentColors()
    return ok(data={"theme": theme, "accent": accent},
              revision=current_revision())


# ── 配信設定 (ADR-005 S4e) ──
_STREAM_FIELDS = ("url", "title")


@api_v1.route("/stream", methods=["PUT"])
@write_op("stream.update")
def stream_update():
    # {"url": str, "title": str}, either or both: the stream destination
    # and the broadcast title (state, stream.*). Whether a title being typed
    # beats an auto-generated one is the client's to decide (StreamService.
    # TypedTitleWins); what is sent is written.
    body = _json_body()
    unknown = sorted(k for k in body if k not in _STREAM_FIELDS)
    if unknown:
        raise ApiError(
            INVALID_ARGUMENT, f"unknown stream field: {', '.join(unknown)}",
            400)
    if not body:
        raise ApiError(INVALID_ARGUMENT, "url or title is required", 400)
    for key, value in body.items():
        if not isinstance(value, str):
            raise ApiError(INVALID_ARGUMENT, f"{key} must be a string", 400)
    WebOps.update_stream(body)
    return ok(revision=current_revision())


def _stream_channel_fields():
    # {"name": str, "url": str}, both non-empty (StreamService refuses an
    # empty one; refused here first, so the answer says which).
    body = _json_body()
    unknown = sorted(k for k in body if k not in ("name", "url"))
    if unknown:
        raise ApiError(
            INVALID_ARGUMENT, f"unknown channel field: {', '.join(unknown)}",
            400)
    fields = []
    for key in ("name", "url"):
        value = body.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ApiError(
                INVALID_ARGUMENT, f"{key} must be a non-empty string", 400)
        fields.append(value)
    return fields


@api_v1.route("/stream/channels", methods=["POST"])
@write_op("stream.channel.add")
def stream_channel_add():
    # Saved at the end; answered with its index and the whole list.
    # Settings, not state: no revision.
    name, url = _stream_channel_fields()
    index = WebOps.add_stream_channel(name, url)
    if index is None:
        raise ApiError(
            INVALID_ARGUMENT, "name and url must be non-empty strings", 400)
    return ok(data={"index": index,
                    "channels": WebOps.get_stream_channels()})


@api_v1.route("/stream/channels/<int:index>", methods=["PUT"])
@write_op("stream.channel.update")
def stream_channel_update(index):
    # A destination that was this channel's URL follows it (stream.url),
    # hence the revision.
    name, url = _stream_channel_fields()
    if not WebOps.update_stream_channel(index, name, url):
        raise ApiError(NOT_FOUND, f"no stream channel {index}", 404)
    return ok(data={"channels": WebOps.get_stream_channels()},
              revision=current_revision())


@api_v1.route("/stream/channels/<int:index>", methods=["DELETE"])
@write_op("stream.channel.delete")
def stream_channel_delete(index):
    # A destination that was its URL is cleared (stream.url).
    if not WebOps.delete_stream_channel(index):
        raise ApiError(NOT_FOUND, f"no stream channel {index}", 404)
    return ok(data={"channels": WebOps.get_stream_channels()},
              revision=current_revision())


# The image upload (ADR-005 S4 論点 2): {"image": "data:<type>;base64,
# ..."}, the file as it is -- core scales it down (StreamService.
# ImportOrgAsset, TournamentMediaService.ImportMedia) and checks what it
# really is: the data URL's type is the browser's guess from the file name
# (application/octet-stream for one it does not know), so it is not looked
# at. Up to 20MB of image (the pages refuse a larger file before sending
# it: IMAGE_UPLOAD_MAX_BYTES in web/src/PanelShell/imageUpload.ts, pinned
# equal); the body is its base64 (4/3) and a little JSON around it, and a
# longer one is refused unparsed. The stream org's upload (S4e) and the
# tournament media's (S4g) share it.
ORG_IMAGE_MAX_BYTES = 20 * 1024 * 1024
_ORG_IMAGE_MAX_BASE64 = (ORG_IMAGE_MAX_BYTES + 2) // 3 * 4
_ORG_IMAGE_MAX_BODY = _ORG_IMAGE_MAX_BASE64 + 1024
_DATA_URL_PREFIX = "data:"
_DATA_URL_BASE64 = ";base64"


def _org_image_bytes(body):
    image = body.get("image")
    if not isinstance(image, str):
        raise ApiError(
            INVALID_ARGUMENT, "image must be a base64 data URL", 400)
    header, comma, payload = image.partition(",")
    if not comma or not header.startswith(_DATA_URL_PREFIX) \
            or not header.endswith(_DATA_URL_BASE64):
        raise ApiError(
            INVALID_ARGUMENT, "image must be a base64 data URL", 400)
    if len(payload) > _ORG_IMAGE_MAX_BASE64:
        raise ApiError(
            PAYLOAD_TOO_LARGE,
            f"the image is over {ORG_IMAGE_MAX_BYTES} bytes", 413)
    try:
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise ApiError(INVALID_ARGUMENT, "image is not valid base64", 400)
    if not data:
        raise ApiError(INVALID_ARGUMENT, "image is empty", 400)
    if len(data) > ORG_IMAGE_MAX_BYTES:
        raise ApiError(
            PAYLOAD_TOO_LARGE,
            f"the image is over {ORG_IMAGE_MAX_BYTES} bytes", 413)
    return data


@api_v1.route("/stream/org/<kind>", methods=["PUT"])
@write_op("stream.org.import", log_keys_only=True,
          max_body=_ORG_IMAGE_MAX_BODY)
def stream_org_import(kind):
    # The stream org's icon or thumbnail background: saved under layout/
    # and its version published (stream.orgIconVersion / orgThumbVersion),
    # which the page's preview follows.
    if kind not in streams.ORG_ASSETS:
        raise ApiError(
            INVALID_ARGUMENT,
            f"unknown org asset: {kind} (expected one of: "
            f"{', '.join(streams.ORG_ASSETS)})",
            400,
        )
    data = _org_image_bytes(_json_body())
    result = WebOps.import_stream_org_asset(kind, data)
    if result == streams.INVALID_IMAGE:
        raise ApiError(
            INVALID_ARGUMENT,
            "the image could not be read (a PNG, JPEG or BMP of up to "
            f"{streams.ORG_ASSET_MAX_PIXELS} pixels)",
            400,
        )
    if result == streams.SAVE_FAILED:
        raise ApiError(
            INTERNAL,
            "the image could not be saved (is another app holding the "
            "file?)",
            500,
        )
    return ok(revision=current_revision())


@api_v1.route("/stream/org", methods=["DELETE"])
@write_op("stream.org.reset")
def stream_org_reset():
    # Both org assets unregistered (files removed, versions 0). A thumbnail
    # background that was the org's falls back to the tournament banner:
    # the options as saved now come back.
    WebOps.reset_stream_org_assets()
    return ok(data={"thumbnailOptions":
                    WebOps.get_stream_thumbnail_options()},
              revision=current_revision())


@api_v1.route("/stream/thumbnail/options", methods=["PUT"])
@write_op("stream.thumbnail.options")
def stream_thumbnail_options_update():
    # Some of {"source": banner | org, "style": center | netflix,
    # "overlayOrgIcon": bool}; all of them as saved come back. Settings, not
    # state: no revision.
    body = _json_body()
    if not body:
        raise ApiError(
            INVALID_ARGUMENT, "at least one thumbnail option is required",
            400)
    options = WebOps.set_stream_thumbnail_options(body)
    if options is None:
        raise ApiError(
            INVALID_ARGUMENT,
            "the options are source ("
            f"{' | '.join(streams.THUMBNAIL_SOURCES)}), style ("
            f"{' | '.join(streams.THUMBNAIL_STYLES)}) and overlayOrgIcon "
            "(true / false)",
            400,
        )
    return ok(data=options)


# ── サムネイル (ADR-005 S4o) ──
# core/ThumbnailService. thumbnail_config is settings, not state: no revision
# on what only changes those.
# Some switches and numbers per game: far more than any page sends. A longer
# body is refused unparsed.
_THUMBNAIL_SETTINGS_MAX_BODY = 64 * 1024


@api_v1.route("/thumbnail", methods=["PATCH"])
@write_op("thumbnail.update", max_body=_THUMBNAIL_SETTINGS_MAX_BODY)
def thumbnail_update():
    # Some of template_id, background_source, display_phase, use_team_names,
    # use_sponsors and game: {<codename>: {flip_p1, flip_p2, smooth_scale,
    # zoom, align: {horizontal, vertical}}}; all of them as saved come back.
    # A codename is one of the game list's or the state's game. Not a path: a
    # background comes in through POST /thumbnail/background.
    body = _json_body()
    if not body:
        raise ApiError(
            INVALID_ARGUMENT, "at least one thumbnail setting is required",
            400)
    settings = WebOps.update_thumbnail_settings(body)
    if settings is None:
        raise ApiError(
            INVALID_ARGUMENT,
            "the settings are template_id ("
            f"{' | '.join(thumbnails.TEMPLATE_IDS)}), background_source ("
            f"{' | '.join(thumbnails.BACKGROUND_SOURCES)}), display_phase, "
            "use_team_names and use_sponsors (true / false) and game: "
            "{<codename, of a game in the list>: {flip_p1, flip_p2, "
            "smooth_scale (true / false), "
            f"zoom ({thumbnails.ZOOM_RANGE[0]}-{thumbnails.ZOOM_RANGE[1]}), "
            "align: {horizontal, vertical} "
            f"({thumbnails.ALIGN_RANGE[0]}-{thumbnails.ALIGN_RANGE[1]})}}}}",
            400,
        )
    return ok(data=settings)


@api_v1.route("/thumbnail:reset", methods=["POST"])
@write_op("thumbnail.reset")
def thumbnail_reset():
    # Back to the defaults, for every game, the uploaded background included:
    # the settings now come back. Destructive, so {"confirm": true}.
    _require_confirm(_json_body())
    WebOps.reset_thumbnail_settings()
    return ok(data=WebOps.get_thumbnail_settings())


@api_v1.route("/thumbnail/background", methods=["POST"])
@write_op("thumbnail.background", log_keys_only=True,
          max_body=_ORG_IMAGE_MAX_BODY)
def thumbnail_background():
    # {"image": <a data URL>}: saved under user_data/ and selected as the
    # background (background_source "custom"); the settings now come back.
    result = WebOps.import_thumbnail_background(
        _org_image_bytes(_json_body()))
    if result == thumbnails.INVALID_IMAGE:
        raise ApiError(
            INVALID_ARGUMENT,
            "the image could not be read (a PNG, JPEG or BMP of up to "
            f"{ImageImport.MAX_PIXELS} pixels)",
            400,
        )
    if result == thumbnails.SAVE_FAILED:
        raise ApiError(
            INTERNAL,
            "the image could not be saved (is another app holding the "
            "file?)",
            500,
        )
    return ok(data=WebOps.get_thumbnail_settings())


# ── 大会情報 (ADR-005 S4g) ──
# The Event page's: the five fields, the tournament's icon / banner. The set
# / clear is with the heavy operations below; the colours are above
# (/colors/tournament).
# The entrant count's maximum: room for any count start.gg reports, which
# the payload writes as it is (S4g review L7; the Qt tab's spin box stopped
# at 9999). The Event page's field takes the same (ENTRANTS_MAX in
# web/src/EventPage/Cards.tsx, pinned equal).
TOURNAMENT_ENTRANTS_MAX = 999999


@api_v1.route("/tournament/info", methods=["PATCH"])
@write_op("tournament.info")
def tournament_info_update():
    # Some of {"tournamentName", "eventName", "address": str,
    # "numEntrants": 0..999999, "startAtDate": "YYYY-MM-DD" (2000-01-01 to
    # 2999-12-31, ASCII digits)}: tournamentInfo.* through
    # TournamentInfoService, in one save cycle. The date is published as
    # tournamentInfo.startAt (the PC's short date, as before) and
    # startAtDate; it cannot be emptied (the Qt tab's date edit could not
    # either). A day this PC cannot make a time of is refused too, with
    # nothing written (S4g review L4). A payload loaded later replaces
    # them, as it did.
    body = _json_body()
    fields = schema.TOURNAMENT_INPUT_KEYS
    unknown = sorted(k for k in body if k not in fields)
    if unknown:
        raise ApiError(
            INVALID_ARGUMENT,
            f"unknown tournament field: {', '.join(unknown)}", 400)
    if not body:
        raise ApiError(
            INVALID_ARGUMENT, f"one of {', '.join(fields)} is required", 400)
    for key, value in body.items():
        if key in schema.TOURNAMENT_TEXT_FIELDS:
            if not isinstance(value, str):
                raise ApiError(
                    INVALID_ARGUMENT, f"{key} must be a string", 400)
        elif key in schema.TOURNAMENT_NUMBER_FIELDS:
            if (isinstance(value, bool) or not isinstance(value, int)
                    or not 0 <= value <= TOURNAMENT_ENTRANTS_MAX):
                raise ApiError(
                    INVALID_ARGUMENT,
                    f"{key} must be an integer from 0 to "
                    f"{TOURNAMENT_ENTRANTS_MAX}", 400)
        elif TournamentInfoService.ParseDate(value) is None:
            raise ApiError(
                INVALID_ARGUMENT,
                f"{key} must be a day, YYYY-MM-DD, from 2000-01-01 to "
                "2999-12-31", 400)
    if not WebOps.update_tournament_info(body):
        raise ApiError(
            INVALID_ARGUMENT,
            "startAtDate cannot be converted to a time on this PC", 400)
    return ok(revision=current_revision())


@api_v1.route("/tournament/media/<kind>", methods=["PUT"])
@write_op("tournament.media.import", log_keys_only=True,
          max_body=_ORG_IMAGE_MAX_BODY)
def tournament_media_import(kind):
    # The tournament's icon (layout/logo.png, scaled to 512) or banner
    # (user_data/tournament_banner.png, 1920), as PNG: its version published
    # (tournamentInfo.logoVersion / bannerVersion), which the previews and
    # the overlays follow. The upload as the stream org's (above).
    if kind not in tournamentMedia.MEDIA:
        raise ApiError(
            INVALID_ARGUMENT,
            f"unknown tournament media: {kind} (expected one of: "
            f"{', '.join(tournamentMedia.MEDIA)})",
            400,
        )
    data = _org_image_bytes(_json_body())
    result = WebOps.import_tournament_media(kind, data)
    if result == tournamentMedia.INVALID_IMAGE:
        raise ApiError(
            INVALID_ARGUMENT,
            "the image could not be read (a PNG, JPEG or BMP of up to "
            f"{ImageImport.MAX_PIXELS} pixels)",
            400,
        )
    if result == tournamentMedia.SAVE_FAILED:
        raise ApiError(
            INTERNAL,
            "the image could not be saved (is another app holding the "
            "file?)",
            500,
        )
    return ok(revision=current_revision())


@api_v1.route("/tournament/media", methods=["DELETE"])
@write_op("tournament.media.reset")
def tournament_media_reset():
    # Both back to Autona's own: the icon is Autona's icon again, the banner
    # is removed (its readers fall back to the bundled default banner).
    # Both versions published in one save cycle.
    if not WebOps.reset_tournament_media():
        raise ApiError(
            INTERNAL,
            "the tournament media could not be reset (is another app "
            "holding the file?)",
            500,
        )
    return ok(revision=current_revision())


@api_v1.route("/tournament/media/fetch", methods=["POST"])
@write_op("tournament.media.fetch")
def tournament_media_fetch():
    # The loaded tournament's icon and banner from start.gg, by a background
    # job (TournamentMediaService.FetchMedia): tournamentInfo.media says
    # "running" before this answers, and "done" or "error" (with why) once
    # it is over. One at a time: a fetch for this tournament running already
    # is 409; while another tournament's runs, this one is queued behind it
    # (202, "queued": true) and runs when that one ends.
    started = WebOps.fetch_tournament_media()
    if started == tournamentMedia.BUSY:
        raise ApiError(
            CONFLICT, "the tournament media are being fetched already", 409)
    if started == tournamentMedia.NO_TOURNAMENT:
        raise ApiError(NOT_READY, "no tournament loaded", 409)
    return ok(data={"accepted": True, "operation": "tournament.media.fetch",
                    "queued": started == tournamentMedia.QUEUED},
              revision=current_revision(), status=202)


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


@api_v1.route("/players:delete", methods=["POST"])
@write_op("player.delete", log_keys_only=True)
def player_delete():
    # {"key", "confirm": true}: delete the player stored under `key` (a
    # row's, GET /players) from the local database (ADR-005 S4m). The
    # database, not state: no revision. 404 when it has no such key.
    body = _json_body()
    _require_confirm(body)
    key = body.get("key")
    if not isinstance(key, str) or not key:
        raise ApiError(INVALID_ARGUMENT, "key must be a non-empty string", 400)
    if not WebOps.delete_player(key):
        raise ApiError(NOT_FOUND, "no player with that key", 404)
    return ok()


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
    # Core's since ADR-005 S3e-7 (SetFollowService), in both shells. With
    # no tournament there is nothing to load, and the board is left as it
    # is (the legacy route answers "OK").
    if AutonaTournamentDataProvider.instance.GetProvider() is None:
        raise ApiError(NOT_READY, "no tournament loaded", 409)
    WebOps.load_set(n, str(set_id), no_mains=no_mains)
    return ok(revision=current_revision())


@api_v1.route("/scoreboards/<int:n>:pull-stream", methods=["POST"])
@write_op("pull-stream")
def pull_stream(n):
    # The streamer's channel's next set, followed (ADR-005 S3e-7 論点 3:
    # it did nothing). Without a tournament or a channel there is nothing
    # to follow -- NOT_READY here, where the legacy route answers "OK".
    require_board_12(n)
    if AutonaTournamentDataProvider.instance.GetProvider() is None:
        raise ApiError(NOT_READY, "no tournament loaded", 409)
    if WebOps.stream_channel() is None:
        raise ApiError(NOT_READY, "twitch_username is not set", 409)
    WebOps.pull_stream_set(n)
    return ok(revision=current_revision())


# ── Heavy operations (202 + in-progress flag, spec §2.8 / §7.3) ──
@api_v1.route("/tournament", methods=["POST"])
@write_op("tournament.load")
def tournament_load():
    # {"url": str | null, "force": bool, "fetchMedia": bool}. A start.gg
    # event URL or, since ADR-005 S4g, an admin/brackets one (resolved to
    # its event by a background job); empty (or null, or none) clears the
    # tournament; anything else is refused (S4g review L3: any false value
    # used to clear). The loaded URL again is not re-fetched unless
    # "force"; "fetchMedia" fetches the tournament's icon and banner once
    # it is loaded. Both default to false: what this route always did.
    #
    # How it goes is the state's tournamentInfo.load, published before this
    # answers (on this thread, S4g review M1): the 202's "loadRevision" is
    # the request's revision there (or a later request's, see
    # WebOps.set_tournament).
    body = _json_body()
    url = body.get("url")
    if url is None:
        url = ""
    if not isinstance(url, str) or (url and tournaments.UrlKind(url) is None):
        raise ApiError(
            INVALID_ARGUMENT,
            "url must be a start.gg tournament event URL or an admin "
            "brackets URL", 400
        )
    flags = {}
    for name in ("force", "fetchMedia"):
        value = body.get(name, False)
        if not isinstance(value, bool):
            raise ApiError(
                INVALID_ARGUMENT, f"{name} must be true or false", 400)
        flags[name] = value
    if not _heavy.try_begin("tournament"):
        raise ApiError(CONFLICT, "tournament load already in progress", 409)

    try:
        # Handed to core's TournamentService (ADR-005 S4f), which publishes
        # the request's status here and normalizes, saves and loads the URL
        # on the GUI thread (the GUI executor; in place when this runs
        # there). An empty url clears the tournament (and forgets the
        # selected game, as it always did here).
        loadRevision = WebOps.set_tournament(url, **flags)
        # The flag is released by the GUI thread, after the apply queued
        # just ahead of it (the GUI executor runs what one thread hands it
        # in order): by then the provider switch is done (the start.gg
        # fetches it starts are async and reported over WS) -- or nothing
        # happened because that URL is already the loaded one. It used to
        # wait for tournament_changed, which that early return never emits,
        # so re-sending the loaded URL held the flag for the guard's full
        # TTL and every retry got 409.
        AutonaQtHelper.gui_executor(lambda: _heavy.end("tournament"))
    except Exception:
        _heavy.end("tournament")
        raise
    return ok(data={"accepted": True, "operation": "tournament.load",
                    "loadRevision": loadRevision},
              revision=current_revision(), status=202)


@api_v1.route("/tournament", methods=["DELETE"])
@write_op("tournament.clear")
def tournament_clear():
    # The tournament unloaded: its URL forgotten, the tournament info
    # emptied, and the selected game forgotten too (TournamentService.
    # ClearTournament, like every clear; ADR-005 S4g). Its idle status is
    # published here, before this answers (its revision: "loadRevision",
    # as POST's), and the clear runs on the GUI thread after whatever set
    # came before it. No in-progress flag: a clear is quick, and a later
    # one wins anyway.
    loadRevision = WebOps.clear_tournament()
    return ok(data={"accepted": True, "operation": "tournament.clear",
                    "loadRevision": loadRevision},
              revision=current_revision(), status=202)


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
        # The sync set_game action waits for a synchronous load
        # (async_mode=False). For 202 we start the async load path instead,
        # dispatched fire-and-forget via the GUI executor (spec §14.3). That
        # was where the desktop's loader QThread had to be created (its
        # parent lived there); the load is a TaskExecutor job since ADR-005
        # S3f-4, which any thread may start, and the dispatch is kept as is.
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


@api_v1.route("/analysis:refresh", methods=["POST"])
@write_op("analysis.refresh")
def analysis_refresh():
    # Fetch the loaded tournament's match list from start.gg (ADR-005 S4k;
    # core's AnalysisService, in either shell), by jobs in the background:
    # analysis says "running" at the next revision before this answers, and
    # "done" or "error" once it is over; the rows are GET /analysis's. One
    # at a time: while one runs, this is 409 and dropped (not queued).
    started = WebOps.refresh_analysis()
    if started == analysis.BUSY:
        raise ApiError(
            CONFLICT, "the match list is being fetched already", 409)
    if started == analysis.NO_TOURNAMENT:
        raise ApiError(NOT_READY, "no tournament loaded", 409)
    return ok(data={"accepted": True, "operation": "analysis.refresh"},
              revision=current_revision(), status=202)


# ── Top 8 (ADR-005 S4i) ──
# The Top 8 page's writes, through Top8Service: one player's fields, the
# overlay's caption, the scores (a merge) and their reset. The import is
# /bracket:refresh above, the colours /colors/top8, the design
# /layouts/selected/top8. Players' names are personal data: logged by key
# only.
def _top8_slot(slot):
    if str(slot) not in top8.PLAYER_SLOTS:
        raise ApiError(
            INVALID_ARGUMENT, f"slot must be 1..{len(top8.PLAYER_SLOTS)}",
            400)
    return str(slot)


def _is_index(value):
    return isinstance(value, int) and not isinstance(value, bool)


_TOP8_PLAYER_FIELDS = (*Top8Service.PLAYER_TEXT_FIELDS, "country",
                       "character")


@api_v1.route("/top8/players/<int:slot>", methods=["PATCH"])
@write_op("top8.player", log_keys_only=True)
def top8_player_update(slot):
    # Some of {"name", "sponsor": str ("" empties it), "country": a code (a
    # custom flag's name, GET /countries), or null / "" to empty it,
    # "character": {"en_name": str, "skin": its row in GET /characters'
    # skins (default 0; any for a character without skins), "game": the
    # codename of the game whose list it was picked on (optional)}, or null
    # to empty it}: one player of the Top 8, in one save cycle. The values
    # are built by core from the catalogs, as the player cards' are
    # (Top8Service.UpdatePlayer). Nothing is written when any field is
    # refused: 400 for a code, a character or a skin the catalogs do not
    # have (a stray identifier never empties what is on air), 409 NOT_READY
    # for a country before the country data is loaded, 409 CONFLICT for a
    # character whose "game" is not the game loaded (picked on the list of
    # the game before a switch).
    slot = _top8_slot(slot)
    body = _json_body()
    unknown = sorted(k for k in body if k not in _TOP8_PLAYER_FIELDS)
    if unknown:
        raise ApiError(
            INVALID_ARGUMENT, f"unknown player field: {', '.join(unknown)}",
            400)
    if not body:
        raise ApiError(
            INVALID_ARGUMENT,
            f"one of {', '.join(_TOP8_PLAYER_FIELDS)} is required", 400)
    fields = {}
    for key, value in body.items():
        if key in Top8Service.PLAYER_TEXT_FIELDS:
            if not isinstance(value, str):
                raise ApiError(
                    INVALID_ARGUMENT, f"{key} must be a string", 400)
            fields[key] = value
        elif key == "country":
            if value is not None and not isinstance(value, str):
                raise ApiError(
                    INVALID_ARGUMENT,
                    "country must be a country code or null", 400)
            fields[key] = value or None
        elif value is None:
            fields[key] = None
        else:
            if (not isinstance(value, dict)
                    or set(value) - {"en_name", "skin", "game"}
                    or not isinstance(value.get("en_name"), str)
                    or not value["en_name"]):
                raise ApiError(
                    INVALID_ARGUMENT,
                    'character must be {"en_name": str, "skin": int, '
                    '"game": str} or null', 400)
            skin = value.get("skin", 0)
            if not _is_index(skin) or skin < 0:
                raise ApiError(
                    INVALID_ARGUMENT,
                    "character.skin must be an integer from 0", 400)
            game = value.get("game")
            if game is not None and not isinstance(game, str):
                raise ApiError(
                    INVALID_ARGUMENT, "character.game must be a string", 400)
            fields[key] = (value["en_name"], skin, game)
    if fields.get("country") and not CountryCatalog.instance.IsLoaded():
        raise ApiError(NOT_READY, "country data is not loaded yet", 409)
    refused = WebOps.update_top8_player(slot, fields)
    if refused == Top8Service.UNKNOWN_COUNTRY:
        raise ApiError(
            INVALID_ARGUMENT, f"unknown country: {fields['country']}", 400)
    if refused == Top8Service.UNKNOWN_CHARACTER:
        raise ApiError(
            INVALID_ARGUMENT,
            f"no character {fields['character'][0]} in the loaded game", 400)
    if refused == Top8Service.UNKNOWN_SKIN:
        raise ApiError(
            INVALID_ARGUMENT,
            f"no skin {fields['character'][1]} for "
            f"{fields['character'][0]}", 400)
    if refused == Top8Service.OTHER_GAME:
        raise ApiError(
            CONFLICT,
            f"{fields['character'][0]} was picked for the game "
            f"{fields['character'][2]!r}, not the loaded one", 409)
    return ok(revision=current_revision())


@api_v1.route("/top8", methods=["PATCH"])
@write_op("top8.update")
def top8_update():
    # {"phaseLabel": str}: the overlay's caption; empty is the default
    # ("TOP 8"). Answers what was published.
    body = _json_body()
    unknown = sorted(k for k in body if k != "phaseLabel")
    if unknown:
        raise ApiError(
            INVALID_ARGUMENT, f"unknown Top 8 field: {', '.join(unknown)}",
            400)
    if "phaseLabel" not in body:
        raise ApiError(INVALID_ARGUMENT, "phaseLabel is required", 400)
    if not isinstance(body["phaseLabel"], str):
        raise ApiError(INVALID_ARGUMENT, "phaseLabel must be a string", 400)
    label = WebOps.set_top8_phase_label(body["phaseLabel"])
    return ok(data={"phaseLabel": label}, revision=current_revision())


@api_v1.route("/top8/scores", methods=["POST"])
@write_op("top8.scores")
def top8_scores_apply():
    # {match id: [s1, s2]} for some of the Top 8's matches (wsf1 ... gf,
    # gf_reset), each two integers 0..99: applied (反映) as a merge -- the
    # matches not sent keep their scores and results -- and the winners
    # carried on (Top8Service.ApplyScores). A tie is not a result. An
    # unknown match or a bad score refuses the whole request. Answers the
    # matches published.
    body = _json_body()
    if not body:
        raise ApiError(INVALID_ARGUMENT, "at least one match is required",
                       400)
    unknown = sorted(k for k in body if k not in top8.MATCH_IDS)
    if unknown:
        raise ApiError(
            INVALID_ARGUMENT,
            f"unknown match: {', '.join(unknown)} (expected some of: "
            f"{', '.join(top8.MATCH_IDS)})", 400)
    for matchId, score in body.items():
        if (not isinstance(score, list) or len(score) != 2
                or not all(_is_index(s) and 0 <= s <= Top8Service.SCORE_MAX
                           for s in score)):
            raise ApiError(
                INVALID_ARGUMENT,
                f"{matchId} must be two integers from 0 to "
                f"{Top8Service.SCORE_MAX}", 400)
    matches = WebOps.apply_top8_scores(body)
    if matches is None:
        raise ApiError(INVALID_ARGUMENT, "the scores were refused", 400)
    return ok(data=matches, revision=current_revision())


@api_v1.route("/top8/scores", methods=["DELETE"])
@write_op("top8.scores.reset")
def top8_scores_reset():
    # Every match's score and result back to none (全リセット). Answers the
    # matches published.
    return ok(data=WebOps.reset_top8_scores(), revision=current_revision())


@api_v1.route("/stream/thumbnail", methods=["POST"])
@write_op("stream.thumbnail")
def stream_thumbnail_generate():
    # A stream thumbnail with the saved options, made by a background job
    # (StreamService.GenerateThumbnail; ADR-005 S4e): stream.thumbnail says
    # "running" before this answers, and "done" (its path, out/stream/...)
    # or "error" once the job is over. One at a time: the in-progress flag
    # is core's, shared with every other way in.
    if WebOps.generate_stream_thumbnail() == streams.BUSY:
        raise ApiError(
            CONFLICT, "a stream thumbnail is being made already", 409)
    return ok(data={"accepted": True, "operation": "stream.thumbnail"},
              revision=current_revision(), status=202)


@api_v1.route("/thumbnail:preview", methods=["POST"])
@write_op("thumbnail.preview")
def thumbnail_preview():
    # Draw the preview image, with the saved settings, in the background
    # (ThumbnailService.Preview; ADR-005 S4o). thumbnail.render says "running"
    # and then "done" (GET /thumbnail/preview has it) or "error", with
    # `renderRevision`, the revision of the run that does this: requests
    # during a run are drawn together by the next one. The desktop's: 503
    # UNAVAILABLE headless.
    return ok(data={"accepted": True, "operation": "thumbnail.preview",
                    "renderRevision": WebOps.preview_thumbnail()},
              revision=current_revision(), status=202)


@api_v1.route("/thumbnail:generate", methods=["POST"])
@write_op("thumbnail.generate")
def thumbnail_generate():
    # Draw and save a thumbnail (out/thumbnails, a PNG and a JPG) in the
    # background: as above, and thumbnail.render's path is the PNG once it is
    # "done". 409 CONFLICT while one is on its way.
    revision = WebOps.generate_thumbnail()
    if revision == thumbnails.BUSY:
        raise ApiError(CONFLICT, "a thumbnail is being made already", 409)
    return ok(data={"accepted": True, "operation": "thumbnail.generate",
                    "renderRevision": revision},
              revision=current_revision(), status=202)


# ── The tournament result (ADR-005 S4q): core/ResultService ──
# Every body is a small JSON object: far more than a page's row would ever be,
# and far less than the state and settings.json should be asked to hold.
_RESULT_MAX_BODY = 64 * 1024
_RESULT_KINDS = (results.PLAYERS, results.TEAMS)
_RESULT_PLAYER_FIELDS = ("rank", *results.PLAYER_TEXT_FIELDS, "characters")
_RESULT_TEAM_FIELDS = ("rank", "name", "members")
_RESULT_MEMBER_FIELDS = ("name", "twitter", "character")
_RESULT_PICK_SHAPE = ('{"en_name": str, "skin": int, "game": str} or null')


def _result_slot(slot, kind):
    count = results.SLOT_COUNTS[kind]
    if not 1 <= slot <= count:
        raise ApiError(INVALID_ARGUMENT, f"slot must be 1..{count}", 400)
    return str(slot)


def _result_rank(value):
    low, high = results.RANK_RANGE
    if not _is_index(value) or not low <= value <= high:
        raise ApiError(
            INVALID_ARGUMENT, f"rank must be an integer from {low} to {high}",
            400)
    return value


def _result_text(value, name):
    if not isinstance(value, str):
        raise ApiError(INVALID_ARGUMENT, f"{name} must be a string", 400)
    return value


def _result_pick(value, name):
    # A character as a page picks it: (en_name, skin row, the game it was
    # picked on | None), or None for an empty one.
    if value is None:
        return None
    if (not isinstance(value, dict)
            or set(value) - {"en_name", "skin", "game"}
            or not isinstance(value.get("en_name"), str)
            or not value["en_name"]):
        raise ApiError(
            INVALID_ARGUMENT, f"{name} must be {_RESULT_PICK_SHAPE}", 400)
    skin = value.get("skin", 0)
    if not _is_index(skin) or skin < 0:
        raise ApiError(
            INVALID_ARGUMENT, f"{name}.skin must be an integer from 0", 400)
    game = value.get("game")
    if game is not None and not isinstance(game, str):
        raise ApiError(INVALID_ARGUMENT, f"{name}.game must be a string", 400)
    return value["en_name"], skin, game


def _result_places(value, count, name):
    # The places to change, by number from "1", of what stays otherwise.
    if not value or set(value) - {str(n) for n in range(1, count + 1)}:
        raise ApiError(
            INVALID_ARGUMENT,
            f"{name} must name places from 1 to {count}", 400)
    return value


def _result_member(member):
    # The keys it names: as a list's element the others are empty, as a place's
    # they stay as they are.
    if not isinstance(member, dict) \
            or set(member) - set(_RESULT_MEMBER_FIELDS):
        raise ApiError(
            INVALID_ARGUMENT,
            f"a member must be an object of "
            f"{', '.join(_RESULT_MEMBER_FIELDS)}", 400)
    return {
        field: _result_pick(value, "a member's character")
        if field == "character" else _result_text(value, field)
        for field, value in member.items()}


def _result_refused(refused):
    # What UpdatePlayer / UpdateTeam refused: nothing was written.
    if refused is None:
        return
    en_name, skin, game = refused.pick
    if refused.code == results.UNKNOWN_SKIN:
        raise ApiError(INVALID_ARGUMENT, f"no skin {skin} for {en_name}", 400)
    if refused.code == results.OTHER_GAME:
        raise ApiError(
            CONFLICT,
            f"{en_name} was picked for the game {game!r}, not the loaded one",
            409)
    raise ApiError(
        INVALID_ARGUMENT, f"no character {en_name} in the loaded game", 400)


def _result_fields(body, allowed):
    unknown = sorted(k for k in body if k not in allowed)
    if unknown:
        raise ApiError(
            INVALID_ARGUMENT, f"unknown field: {', '.join(unknown)}", 400)
    if not body:
        raise ApiError(
            INVALID_ARGUMENT, f"one of {', '.join(allowed)} is required", 400)


def _result_kind(body):
    kind = body.get("kind")
    if kind not in _RESULT_KINDS:
        raise ApiError(
            INVALID_ARGUMENT, f"kind must be {' | '.join(_RESULT_KINDS)}", 400)
    return kind


@api_v1.route("/result", methods=["PATCH"])
@write_op("result.update", max_body=_RESULT_MAX_BODY)
def result_update():
    # {"layout": one of GET /result's}: the layout, in settings.json. The
    # paths of result_config are not taken: a path would have the render read
    # any local file. Answers the layout.
    body = _json_body()
    _result_fields(body, ("layout",))
    if not WebOps.set_result_layout(body["layout"]):
        raise ApiError(
            INVALID_ARGUMENT,
            f"layout must be {' | '.join(results.LAYOUT_SUBDIRS)}", 400)
    return ok(data={"layout": WebOps.get_result_layout()},
              revision=current_revision())


@api_v1.route("/result/players/<int:slot>", methods=["PATCH"])
@write_op("result.player", log_keys_only=True, max_body=_RESULT_MAX_BODY)
def result_player_update(slot):
    # Some of {"rank": int, "name", "sponsor", "twitter": str ("" empties it),
    # "characters": up to three of {"en_name", "skin", "game"} or null for an
    # empty one, replacing them all, or {"1".."3": the same}, changing those
    # places only}: one row of the singles' layouts, in one save cycle
    # (ResultService.UpdatePlayer). The values are built by core
    # from the catalog. Nothing is written when anything is refused: 400 for a
    # bad field or a character the catalog does not have, 409 CONFLICT for one
    # whose "game" is not the game loaded.
    slot = _result_slot(slot, results.PLAYERS)
    body = _json_body()
    _result_fields(body, _RESULT_PLAYER_FIELDS)
    fields = {}
    for key, value in body.items():
        if key == "rank":
            fields[key] = _result_rank(value)
        elif key == "characters" and isinstance(value, dict):
            fields[key] = {
                place: _result_pick(pick, "a character")
                for place, pick in _result_places(
                    value, results.MAX_CHARACTERS, key).items()}
        elif key == "characters":
            if not isinstance(value, list) \
                    or len(value) > results.MAX_CHARACTERS:
                raise ApiError(
                    INVALID_ARGUMENT,
                    f"characters must be a list of up to "
                    f"{results.MAX_CHARACTERS}", 400)
            fields[key] = [_result_pick(pick, "a character") for pick in value]
        else:
            fields[key] = _result_text(value, key)
    _result_refused(WebOps.update_result_player(slot, fields))
    return ok(revision=current_revision())


@api_v1.route("/result/teams/<int:slot>", methods=["PATCH"])
@write_op("result.team", log_keys_only=True, max_body=_RESULT_MAX_BODY)
def result_team_update(slot):
    # Some of {"rank": int, "name": str, "members": up to five of {"name",
    # "twitter": str, "character": {"en_name", "skin", "game"} or null},
    # replacing them all, or {"1".."5": some of those keys}, changing those keys
    # of those places only}: one row of the team layouts, in one save cycle
    # (ResultService.UpdateTeam). Refused as PATCH /result/players/<n> is.
    slot = _result_slot(slot, results.TEAMS)
    body = _json_body()
    _result_fields(body, _RESULT_TEAM_FIELDS)
    fields = {}
    for key, value in body.items():
        if key == "rank":
            fields[key] = _result_rank(value)
        elif key == "name":
            fields[key] = _result_text(value, key)
        elif isinstance(value, dict):
            fields[key] = {
                place: _result_member(member)
                for place, member in _result_places(
                    value, results.MAX_MEMBERS, key).items()}
        else:
            if not isinstance(value, list) \
                    or len(value) > results.MAX_MEMBERS:
                raise ApiError(
                    INVALID_ARGUMENT,
                    f"members must be a list of up to {results.MAX_MEMBERS}",
                    400)
            fields[key] = [_result_member(member) for member in value]
    _result_refused(WebOps.update_result_team(slot, fields))
    return ok(revision=current_revision())


@api_v1.route("/result:reorder", methods=["POST"])
@write_op("result.reorder", max_body=_RESULT_MAX_BODY)
def result_reorder():
    # {"kind": "players" | "teams", "order": the slots of the rows, in their
    # new order, each of 1..its length once}: those rows put in that order and
    # ranked 1.. (a drag on the page), in one save cycle. The rows after them
    # are left alone.
    body = _json_body()
    unknown = sorted(k for k in body if k not in ("kind", "order"))
    if unknown:
        raise ApiError(
            INVALID_ARGUMENT, f"unknown field: {', '.join(unknown)}", 400)
    kind = _result_kind(body)
    order = body.get("order")
    if not isinstance(order, list) or not all(_is_index(o) for o in order) \
            or not WebOps.reorder_result(kind, order):
        raise ApiError(
            INVALID_ARGUMENT,
            "order must hold each of 1..its length once, up to "
            f"{results.SLOT_COUNTS[kind]}", 400)
    return ok(revision=current_revision())


@api_v1.route("/result:clear", methods=["POST"])
@write_op("result.clear", max_body=_RESULT_MAX_BODY)
def result_clear():
    # {"kind": "players" | "teams", "confirm": true}: every row of that kind
    # emptied, in one save cycle. The other kind's stay.
    body = _json_body()
    unknown = sorted(k for k in body if k not in ("kind", "confirm"))
    if unknown:
        raise ApiError(
            INVALID_ARGUMENT, f"unknown field: {', '.join(unknown)}", 400)
    kind = _result_kind(body)
    _require_confirm(body)
    WebOps.clear_result(kind)
    return ok(revision=current_revision())


@api_v1.route("/result:loadStandings", methods=["POST"])
@write_op("result.loadStandings", max_body=_RESULT_MAX_BODY)
def result_load_standings():
    # Put the event's standings in the current layout's rows, by a job in the
    # background (ResultService.LoadStandings), in either shell:
    # result_standings says "running" before this answers, then "done" or
    # "error" ("fetch_failed": start.gg did not answer; "no_standings": it
    # answered, the event has none yet; "changed": the game or the tournament
    # was switched meanwhile) with `standingsRevision`, nothing written unless
    # "done". {"confirm": true} is required when the rows hold something it
    # would overwrite. 409 NOT_READY without a tournament; 409 CONFLICT while
    # one is on its way.
    body = _json_body()
    unknown = sorted(k for k in body if k != "confirm")
    if unknown:
        raise ApiError(
            INVALID_ARGUMENT, f"unknown field: {', '.join(unknown)}", 400)
    if AutonaTournamentDataProvider.instance.GetProvider() is None:
        raise ApiError(NOT_READY, "no tournament loaded", 409)
    revision = WebOps.load_result_standings(_truthy(body.get("confirm")))
    if revision == results.NEEDS_CONFIRMATION:
        raise ApiError(
            CONFIRMATION_REQUIRED, "this operation requires confirm:true", 409)
    if revision == results.BUSY:
        raise ApiError(CONFLICT, "the standings are being loaded already", 409)
    if revision == results.NO_TOURNAMENT:
        # Cleared between the check above and the start.
        raise ApiError(NOT_READY, "no tournament loaded", 409)
    return ok(data={"accepted": True, "operation": "result.loadStandings",
                    "standingsRevision": revision},
              revision=current_revision(), status=202)


@api_v1.route("/result:saveToDb", methods=["POST"])
@write_op("result.saveToDb", max_body=_RESULT_MAX_BODY)
def result_save_to_db():
    # The current layout's rows merged into the player database: what a player
    # has there and the rows do not stay. Answers {"saved": how many}.
    return ok(data={"saved": WebOps.save_result_to_db()})


@api_v1.route("/result:preview", methods=["POST"])
@write_op("result.preview", max_body=_RESULT_MAX_BODY)
def result_preview():
    # Draw the preview image in the background (ResultService.Preview), the
    # sample while no row has anything to draw. As POST /thumbnail:preview:
    # result_render says how it goes, with `renderRevision`. 503 UNAVAILABLE
    # headless.
    return ok(data={"accepted": True, "operation": "result.preview",
                    "renderRevision": WebOps.preview_result()},
              revision=current_revision(), status=202)


@api_v1.route("/result:generate", methods=["POST"])
@write_op("result.generate", max_body=_RESULT_MAX_BODY)
def result_generate():
    # Draw and save the result (out/results, a PNG) in the background, and put
    # the rows' players in the player database: as above, and
    # result_render's path is the PNG once it is "done". 400 INVALID_ARGUMENT
    # with nothing to draw, 409 CONFLICT while one is on its way.
    revision = WebOps.generate_result()
    if revision == results.NO_DATA:
        raise ApiError(INVALID_ARGUMENT, "no row has anything to draw", 400)
    if revision == results.BUSY:
        raise ApiError(CONFLICT, "a result is being made already", 409)
    return ok(data={"accepted": True, "operation": "result.generate",
                    "renderRevision": revision},
              revision=current_revision(), status=202)
