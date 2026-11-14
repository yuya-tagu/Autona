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
from urllib.parse import unquote

from flask import Blueprint, Response, jsonify, request
from loguru import logger
from werkzeug.exceptions import ClientDisconnected

from .core.StateManager import StateManager
from .core.Actions import AutonaActions
from .core import AnalysisService as analysis
from .core import AppSettings
from .core import AssetPackService as assets
from .core import HotkeyRules
from .core import StateSchema as schema
from .core import ColorService
from .core import ImageImport
from .core import CommentaryModel as comms
from .core import CommentaryService
from .core import LayoutCatalog
from .core import PlayerDirectory
from .core import PlayerService
from .core import ResultService as results
from .core import SetSelectionService as selection
from .core import StreamService as streams
from .core import ThumbnailService as thumbnails
from .core import Top8Model as top8
from .core import Top8Service
from .core import TournamentMediaService as tournamentMedia
from .core import TournamentService as tournaments
from .core.CrewBattleService import CrewBattleService
from .core.GameAssetCatalog import GameAssetCatalog
from .core.TournamentInfoService import TournamentInfoService
from .core.CountryCatalog import CountryCatalog
from .core.SettingsManager import SettingsUnreadable
from .core.StatusService import StatusService
# Importing these builds nothing (agent-pitfalls #1): their singletons
# (`.instance`) are created by src/bootstrap.py and read per request.
from .AutonaTournamentDataProvider import AutonaTournamentDataProvider
from .Helpers import AutonaQtHelper
from .Helpers.AutonaDirHelper import AutonaResolve
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
    """Raised by handlers; converted to an error envelope by the wrappers.
    ``reason``: core's fixed code for the refusal (``error.reason``; the
    asset routes', ADR-005 S-final SF-4b-1), None for none."""

    def __init__(self, code, message, status, reason=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.reason = reason


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


def err(code, message, status, reason=None):
    """Error envelope: ``{"ok": false, "error": {"code", "message"}}``, and
    ``error.reason`` when given (core's fixed code: added, so a client that
    reads only ``code`` reads the same)."""
    error = {"code": code, "message": message}
    if reason is not None:
        error["reason"] = reason
    resp = jsonify({"ok": False, "error": error})
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
    """Board 1 (1v1) and 2 (team) only: what a crew board has no such thing
    for (player cards, set loading, statistics, a score of its own: spec
    §2.12). Rejected on board 3 rather than crashing.
    """
    validate_scoreboard(n)
    if n == WebOps.CREW_BOARD:
        raise ApiError(
            INVALID_ARGUMENT,
            "crew board (3) does not support this operation",
            400,
        )
    return n


def require_crew_board(n):
    """Board 3 only: a roster of members with stocks (ADR-005 S4w). Boards
    1/2 have none, and are rejected as board 3 is from their operations."""
    validate_scoreboard(n)
    if n != WebOps.CREW_BOARD:
        raise ApiError(
            INVALID_ARGUMENT, "only the crew board (3) has this operation", 400)
    return n


def _crew_member(n, t, m):
    """A member's address: the crew board, team 1 or 2, member 1..MAX_MEMBERS.
    All nine rows exist whatever the member count is, which only decides how
    many are shown and counted; past them there is no such member."""
    require_crew_board(n)
    _validate_team(t)
    if m < 1:
        raise ApiError(INVALID_ARGUMENT, "member must be 1 or greater", 400)
    if m > CrewBattleService.MAX_MEMBERS:
        raise ApiError(NOT_FOUND, f"team {t} has no member {m}", 404)


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
    # Through AutonaResolve, as the window title reads it: a release has
    # assets/versions.json only under _internal/ (sys._MEIPASS), not next to
    # the exe -- read from the working directory, /health said "?" there.
    global _version_cache
    if _version_cache is None:
        try:
            with open(AutonaResolve("./assets/versions.json"),
                      encoding="utf-8") as f:
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
            return err(e.code, e.message, e.status, e.reason)
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

    Answering without reading it would leave the body unread on the socket.
    werkzeug's development server drains it on its own after the answer
    (up to 10 GB, but only while the next data arrives within 10 ms), so a
    client that sends steadily still gets the 413 and a very slow one may
    see a connection reset instead; reading it here does that drain in the
    route, to its end, rather than leaving it to the server's policy.
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


# write_op's ``body``: the JSON body logged as params (read by _safe_params),
# or none read at all (the route reads its raw body itself: an upload).
BODY_JSON = "json"
BODY_NONE = "none"

# The view args the op log cuts to their first 6 characters (an upload's id
# names what only the shared view's page may import or drop: the line keeps
# enough to tell requests apart).
_REDACTED_VIEW_ARGS = ("upload_id",)
_REDACTED_KEEP = 6


def _logged_path():
    path = request.path
    for name in _REDACTED_VIEW_ARGS:
        value = (request.view_args or {}).get(name)
        if isinstance(value, str) and len(value) > _REDACTED_KEEP:
            path = path.replace(value, value[:_REDACTED_KEEP] + "…")
    return path


def _raw_params():
    # A body the route reads itself: its size alone, never a byte of it.
    length = request.content_length
    return "<no body>" if length is None else f"<{length} bytes>"


def write_op(operation, log_keys_only=False, max_body=None, body=BODY_JSON):
    """Write endpoints: exception->envelope conversion plus an INFO op-log
    line recording IP / method / path / params / result (spec §2.7).

    ``max_body``: this route's request body limit in bytes, answered 413
    PAYLOAD_TOO_LARGE when the Content-Length is over it, the body dropped
    rather than parsed (_over_limit). The app sets no limit of its own (no
    MAX_CONTENT_LENGTH), so the other routes are as they were.

    ``body``: BODY_JSON (the default) logs the JSON body's params
    (_safe_params, which reads the body). BODY_NONE reads no byte of it --
    the route reads its raw body itself (the asset upload, ADR-005 S-final
    SF-4b-1), or refuses it unread -- and logs its Content-Length alone.
    An unread body is then werkzeug's: its server closes every connection
    (it sends ``Connection: close`` on every answer) and, after the answer,
    reads the rest of the body and drops it while it keeps arriving (up to
    10 GB), so the client gets the answer rather than a reset -- nothing is
    written, the request's thread is held that long. Every error answer
    also says ``Connection: close`` itself: under werkzeug a repeat, for a
    server that would keep the connection and read the unread body as the
    next request. Every line cuts the path's _REDACTED_VIEW_ARGS to their
    first 6 characters."""
    if body not in (BODY_JSON, BODY_NONE):
        raise ValueError(f"write_op body: {body!r}")

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            path = _logged_path()
            # Before anything reads the body: _safe_params below does (and
            # caches it for the handler's read).
            if max_body is not None and _over_limit(max_body):
                _oplog.info(
                    f"{request.remote_addr} {request.method} {path} "
                    f"op={operation} params=<over {max_body} bytes> -> 413 "
                    f"{PAYLOAD_TOO_LARGE}"
                )
                return err(PAYLOAD_TOO_LARGE,
                           f"the request body is over {max_body} bytes", 413)
            if body == BODY_NONE:
                params = _raw_params()
            else:
                params = _safe_params(keys_only=log_keys_only)

            def refused(resp):
                # werkzeug's server says it on every answer already (a
                # repeat there); for one that would read the unread body as
                # the next request.
                if body == BODY_NONE:
                    resp.headers["Connection"] = "close"
                return resp

            try:
                resp = fn(*args, **kwargs)
                status = getattr(resp, "status_code", 200)
                _oplog.info(
                    f"{request.remote_addr} {request.method} {path} "
                    f"op={operation} params={params} -> {status}"
                )
                return resp
            except ApiError as e:
                _oplog.info(
                    f"{request.remote_addr} {request.method} {path} "
                    f"op={operation} params={params} -> {e.status} {e.code}"
                    + (f" ({e.reason})" if e.reason else "")
                )
                return refused(err(e.code, e.message, e.status, e.reason))
            except NotAvailable as e:
                _oplog.info(
                    f"{request.remote_addr} {request.method} {path} "
                    f"op={operation} params={params} -> 503 {UNAVAILABLE}"
                )
                return refused(err(UNAVAILABLE, e.message, 503))
            except SettingsUnreadable:
                # A core operation that changes what settings.json holds
                # (PrepareWrite / SetMany) while it could not be read at
                # launch and still cannot be opened: nothing was changed.
                _oplog.info(
                    f"{request.remote_addr} {request.method} {path} "
                    f"op={operation} params={params} -> 409 {NOT_READY}"
                )
                return refused(err(NOT_READY,
                                   "settings.json could not be read: nothing "
                                   "was saved", 409))
            except Exception:
                logger.error(traceback.format_exc())
                _oplog.info(
                    f"{request.remote_addr} {request.method} {path} "
                    f"op={operation} params={params} -> 500 INTERNAL"
                )
                return refused(err(INTERNAL, "internal error", 500))

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
    from .AutonaWebServer import WebServer
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
        # The read-only listener the pages' layout previews load from
        # (ADR-005 S-final SF-8a); null when there is none -- the pages then
        # show no preview, never one on this origin.
        "previewPort": WebServer.PreviewPort(),
    }
    return ok(data=data, revision=StateManager.revision)


@api_v1.route("/status")
@api_handler
def get_status():
    # What the app is busy with (ADR-005 S-final SF-2; the desktop footer's
    # source): {"revision": n, "jobs": [{"id", "code", "args", "done",
    # "total"}, ...]}, the last job the one shown. Words are codes the
    # reader translates (core's StatusService.CODES). Its own revision, not
    # the state's: none on the envelope. Socket.IO's `status` carries the
    # same on every change.
    return ok(data=StatusService.Snapshot())


@api_v1.route("/battle-format")
@api_handler
def get_battle_format():
    # {"format": "singles" | "team" | "crew", "board": 1 | 2 | 3}: the format
    # in force and the board it shows, health's activeBoard (ADR-005 S4x).
    # A setting, not the state: no revision.
    return ok(data=WebOps.get_battle_format())


@api_v1.route("/battle-format", methods=["PUT"])
@write_op("battle-format.set")
def battle_format_set():
    # {"format": "singles" | "team" | "crew"}: the sidebar's switch
    # (AutonaActions.SetBattleFormat, ADR-005 S4x), under the board lock on
    # this thread; the desktop's window shows the format's board once the
    # notice queued to its GUI thread runs (before this answered until
    # ADR-005 S4ad). The result's layout follows. `changed`: false for the
    # format in force, which writes nothing. The revision covers what the
    # switch wrote to the state (the result's layout).
    fmt = _json_body().get("format")
    if not isinstance(fmt, str) or fmt not in AutonaActions.FORMAT_BOARDS:
        raise ApiError(
            INVALID_ARGUMENT,
            f"format must be one of: {', '.join(AutonaActions.FORMAT_BOARDS)}",
            400)
    changed = WebOps.set_battle_format(fmt)
    data = WebOps.get_battle_format()
    data["changed"] = bool(changed)
    return ok(data=data, revision=current_revision())


@api_v1.route("/scoreboards/<int:n>")
@api_handler
def get_scoreboard(n):
    validate_scoreboard(n)
    data = _board_data(WebOps.get_scoreboard(n))
    # The live swap flag (score.{n}.teamsSwapped -- on boards 1/2, not the
    # committed view above), read through the legacy operation so both routes
    # answer the same. Tolerate a board that cannot answer.
    try:
        data["swapped"] = WebOps.get_swap(n) == "True"
    except Exception:
        pass
    return ok(data=data, revision=current_revision())


def _board_data(board_state):
    return dict(board_state) if isinstance(board_state, dict) else {"state": board_state}


@api_v1.route("/scoreboards/<int:n>/pending")
@api_handler
def get_scoreboard_pending(n):
    # {held, dirty, board}: what the SCORE page draws its form from (ADR-005
    # S4u). `board` has the shape of GET /scoreboards/{n} and the live values,
    # 反映 or not; that route, /state and program_state stay the published
    # view (ADR-005 D4). `held`: this board holds its edits back, so they
    # wait for :apply (the desktop's boards 1/2). `dirty`: some do now. The
    # Socket.IO `scoreboard_pending` tells when to read this again. Guarded,
    # not READ (WebAccess): edits the operator has not published are not for
    # whoever can reach the port.
    validate_scoreboard(n)
    held, dirty, live, swapped, revision = WebOps.get_pending(n)
    board = _board_data(live)
    board["swapped"] = swapped
    return ok(data={"held": held, "dirty": dirty, "board": board},
              revision=revision)


@api_v1.route("/scoreboards/<int:n>/follow")
@api_handler
def get_scoreboard_follow(n):
    # What the board follows (ADR-005 S4v; core's SetFollowService.Snapshot):
    # {station, poll, pollingEnabled, setId}. `station` and `poll` come apart:
    # with polling off (the desktop's default) a station followed has no poll,
    # and a set loaded by hand keeps the station. Board 3 has no set loading:
    # 400. Reading builds no board. The Socket.IO `scoreboard_follow` tells
    # when station / poll change; `secondsLeft` runs down without one, and
    # `setId` goes with the board's own writes (GET /scoreboards/{n}/pending's
    # notice). READ, as the legacy /scoreboard{n}-get-set is.
    require_board_12(n)
    return ok(data=WebOps.get_follow(n), revision=current_revision())


@api_v1.route("/scoreboards/<int:n>/post")
@api_handler
def get_scoreboard_post(n):
    # A board's X post, as its button builds it (the crew board's since
    # ADR-005 S4w, boards 1/2's since S4x): {"text", "missing"}, `missing`
    # naming what the button refuses to post without ("team1", "team2",
    # "round"; empty when it can). A page reads this in the click that opens
    # the X window, as GET /result/post. Boards 1/2's button also copies the
    # match thumbnail to the clipboard: not this route's. From the live
    # values, 反映 or not, as the button reads them: guarded as
    # /pending is (PLAYERDB, S4x). Reading builds no board.
    validate_scoreboard(n)
    return ok(data=WebOps.get_board_post(n), revision=current_revision())


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


@api_v1.route("/match-names/candidates")
@api_handler
def get_match_name_candidates():
    # What a board's editable combos offer: {"phase", "pool", "match"}, lists
    # in their order (ADR-005 S4w). `?board=1|2|3` (S4x): boards 1/2 add the
    # loaded tournament's pools and entrant rounds ahead of the terms, as
    # their combos do; the crew board and no board are the terms alone.
    # /match-names is the terms as they are, by key and with their
    # templates: the ladder of round names is not in it.
    # Exactly "1", "2" or "3": int() takes " 1", "+1" and a full-width "１"
    # as well (ADR-005 S4x review).
    board = request.args.get("board")
    if board is not None:
        if board not in ("1", "2", "3"):
            raise ApiError(
                INVALID_ARGUMENT, "scoreboard must be 1, 2 or 3", 400)
        board = validate_scoreboard(int(board))
    return ok(data=WebOps.get_match_name_candidates(board))


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
    # the window is built (headless: as it starts), so before that
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
    # The layout catalog the Board / Commentary / Top 8 pages and the Designer
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


@api_v1.route("/layouts/selected/board/obs-path")
@api_handler
def get_board_files():
    # The local files the Board page tells OBS to read (ADR-005 S4s):
    # {"designId": <the selected design, null without one>, "design": <its file,
    # null without one>, "winnerPopup": <the popup's>}. Absolute paths, which
    # name the operator's account: apart from GET /layouts, and guarded like the
    # player DB. Not state: no revision.
    return ok(data=WebOps.get_board_files())


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


# The set selection's lists (ADR-005 S4v; core's SetSelectionService): what the
# Qt set and station windows show. Reading never fetches, as opening a window
# never does: `status` is the state's set_selection.{phases,sets,stations}.status
# and `rows` the newest fetch's that made them, with the `url` of their
# tournament and `fetchedAt` (unix seconds) -- none ("", null, []) unless that is
# the tournament loaded now. An idle list is one to ask for (the POSTs below).
# Tags and entrant names only, no real name, so READ like GET /sets; the wording
# of a row -- " (A, B)" for doubles, the search, the order -- is the reader's.
@api_v1.route("/tournament/phases")
@api_handler
def get_tournament_phases():
    # rows: {id, name, groups: [{id, name, bracketType}]}.
    return ok(data=WebOps.get_tournament_phases(),
              revision=current_revision())


@api_v1.route("/tournament/sets")
@api_handler
def get_tournament_sets():
    # rows: {id, stream, station, phase, pool, round, p1, p2: {name, members}}.
    # `query`: the {phase, finished} they were asked for, in one form: the
    # phase row's id, or "all".
    return ok(data=WebOps.get_tournament_sets(), revision=current_revision())


@api_v1.route("/tournament/stations")
@api_handler
def get_tournament_stations():
    # rows: {type: "station" | "stream", id, identifier, stream}: what
    # POST /scoreboards/{n}:follow takes.
    return ok(data=WebOps.get_tournament_stations(),
              revision=current_revision())


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


@api_v1.route("/settings")
@api_handler
def get_settings():
    # The settings page's (ADR-005 S4ae): {"general": the switches and the
    # default colours by their settings.json names, "network": {port,
    # listenLan (null: not decided yet), listening: {port, lan} | null,
    # restartPending}, "language": {program, gameAsset, fgTerm, choices:
    # [{code, name, englishName}], restartPending}, "hotkeys": {enabled, keys:
    # {action: {value, default, valid, warning}}, vocabulary: {modifiers,
    # keys}, applied}, "loadFailed": settings.json could not be read at
    # launch (the values are the defaults; a save reads it again first, and
    # answers 409 NOT_READY while it still cannot be opened; false again once
    # a save has read it again or moved a corrupt one aside), "movedAside":
    # the file name (not the path) a corrupt settings.json was moved aside to
    # in this run, at launch or at a save, else null}. An
    # allow-list (core's AppSettings / HotkeyRules):
    # never the access token, nor anything else settings.json holds, so READ.
    # Settings, not state: no revision.
    return ok(data=WebOps.get_settings())


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
    validate_scoreboard(n)
    # Boards 1/2: AutonaActions.ResetScore, a held write, applied synchronously
    # but not published. The revision below is therefore the last published one
    # and does not move until 反映 -- same as score:adjust above. Board 3: every
    # member back to full stocks and nobody playing (the board's Reset Stocks,
    # CrewBattleService.ResetStocks), published at once.
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
    # All three boards. On board 3 the crews' colours cross over with them
    # (CrewBattleService.SwapTeams, the board's own swap): spec §6.3.
    validate_scoreboard(n)
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
    validate_scoreboard(n)
    _validate_team(t)
    body = _json_body()
    color = body.get("color")
    if not isinstance(color, str) or not _COLOR_RE.match(color):
        raise ApiError(INVALID_ARGUMENT, "color must be a hex string like #FF0000", 400)
    if n == WebOps.CREW_BOARD:
        # As the crew's colour button keeps it, whichever way it was spelled.
        WebOps.crew_team_color(t, _color_rrggbb(color))
    else:
        WebOps.team_color(n, t, color)
    return ok(revision=current_revision())


def _crew_int(body, name, low, high):
    value = body[name]
    if not _is_index(value) or not low <= value <= high:
        raise ApiError(INVALID_ARGUMENT,
                       f"{name} must be an integer from {low} to {high}", 400)
    return value


def _crew_set_fields(body):
    """PATCH set's body on the crew board, checked before anything is written:
    a value the board's own inputs could not hold is refused, not rounded, and
    `characters` / `losers` (what this board does not have) are refused, not
    ignored -- answering OK for them would say something was done."""
    if not body:
        raise ApiError(INVALID_ARGUMENT, "nothing to set", 400)
    fields = {}
    for key, value in body.items():
        if key in schema.SCORE_MATCH_FIELDS:
            if not isinstance(value, str):
                raise ApiError(INVALID_ARGUMENT, f"{key} must be a string", 400)
            fields[key] = value
        elif key == "bestOf":
            fields[key] = _crew_int(
                body, key, 0, CrewBattleService.BEST_OF_MAX)
        elif key == "players":
            fields[key] = _crew_int(
                body, key, 1, CrewBattleService.MAX_MEMBERS)
        elif key == "teamName":
            team = value.get("team") if isinstance(value, dict) else None
            name = value.get("value") if isinstance(value, dict) else None
            # Nothing else in it either, as in the body itself.
            if not _is_index(team) or team not in (1, 2) \
                    or not isinstance(name, str) \
                    or set(value) - {"team", "value"}:
                raise ApiError(
                    INVALID_ARGUMENT,
                    "teamName must be {team: 1 or 2, value: a string}", 400)
            fields[key] = (team, name)
        else:
            raise ApiError(
                INVALID_ARGUMENT, f"the crew board has no field {key}", 400)
    return fields


@api_v1.route("/scoreboards/<int:n>/set", methods=["PATCH"])
@write_op("set.update")
def set_update(n):
    validate_scoreboard(n)
    body = _json_body()
    if n == WebOps.CREW_BOARD:
        WebOps.crew_update_set(_crew_set_fields(body))
        return ok(revision=current_revision())
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
        # between the calls: under its re-entrant lock, in both shells since
        # ADR-005 S4ad (the calls inside run there again; the desktop ran
        # it on the GUI thread until then). Core-direct writes
        # (score:adjust, a colour) are not board operations and can land in
        # between, as they always could.
        if kwargs or (losers is None and team_name is None):
            WebOps.set_route(n, **kwargs)  # synchronous -> revision accurate
        if losers is not None:
            WebOps.set_team_losers(n, *losers)
        if team_name is not None:
            WebOps.set_route(n, team=team_name[0], teamName=team_name[1])

    WebOps.boardHost.Run(apply)
    return ok(revision=current_revision())


@api_v1.route("/scoreboards/<int:n>/match", methods=["PATCH"])
@write_op("match.update")
def match_update(n):
    # Some of {"phase", "pool", "match": str ("" empties it), "bestOf": an
    # integer 0..99 (0 included)}: board 1/2's match bar as its own inputs
    # write it (AutonaActions.SetMatchField / SetBestOf; ADR-005 S4z), in one
    # save cycle. Not PATCH set, which reads them as a set's payload does
    # (SetLoadService.ApplyMatchInfo: a blank phase or match is "not
    # carried", bestOf 0 too, and match_en is emptied): match_en is not
    # touched here. Checked as the crew board's PATCH set is: an empty body,
    # an unknown key or a value of another type is 400, nothing written. On
    # the desktop the board is held: the revision is the last published one.
    require_board_12(n)
    body = _json_body()
    if not body:
        raise ApiError(INVALID_ARGUMENT, "nothing to set", 400)
    fields = {}
    for key, value in body.items():
        if key in schema.SCORE_MATCH_FIELDS:
            if not isinstance(value, str):
                raise ApiError(INVALID_ARGUMENT, f"{key} must be a string", 400)
            fields[key] = value
        elif key == "bestOf":
            fields[key] = _crew_int(body, key, 0, AutonaActions.BEST_OF_MAX)
        else:
            raise ApiError(
                INVALID_ARGUMENT, f"the match bar has no field {key}", 400)
    WebOps.update_match(n, fields)
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


# The card's fields PATCH .../fields takes (ADR-005 S4z): v1's names (those
# of PATCH players and GET /players) -> the slot's keys.
_PLAYER_FIELD_KEYS = {
    "prefix": schema.PLAYER_SPONSOR_KEY,
    "gamerTag": schema.PLAYER_TAG_KEY,
    "name": schema.PLAYER_REAL_NAME_KEY,
    "twitter": schema.PLAYER_TWITTER_KEY,
    "custom_textbox": schema.PLAYER_NOTE_KEY,
}
_PLAYER_LOCATION_FIELDS = ("country_code", "state_code")


def _player_slot(n, t, p):
    """A board 1/2 player slot's address, checked (400)."""
    require_board_12(n)
    _validate_team(t)
    if p < 1:
        raise ApiError(INVALID_ARGUMENT, "player must be 1 or greater", 400)


@api_v1.route(
    "/scoreboards/<int:n>/teams/<int:t>/players/<int:p>/fields",
    methods=["PATCH"])
@write_op("player.fields", log_keys_only=True)
def player_fields_update(n, t, p):
    # Some of {"prefix", "gamerTag", "name" (the real name), "twitter",
    # "custom_textbox": str ("" empties it; the note's line breaks are "\n"),
    # "country_code": a code of GET /countries ("" empties it), "state_code":
    # a region of that country -- the one sent, else the one the slot shows
    # ("" empties it)}: the slot of state team `t` as the desktop card's
    # inputs write it (PlayerService.Edit; ADR-005 S4z), in one save cycle.
    # Not PATCH players, which loads what it is sent (a blank field is not
    # carried, the local database is merged in, a new name empties the
    # city): here a field is what it is sent as, and a sponsor or tag that
    # changed re-derives the display name and the images (the online avatar
    # kept) and empties the id, as the card does -- the city and the seed
    # stay. Nothing is written when a field is refused: 400 for an unknown
    # key, a value that is not a string, a text that breaks PUT /players'
    # rule (_player_text_rule: a line break outside the note, a length; not
    # trimmed here -- the card does not trim), a country or a region the
    # country data does not have; 409 NOT_READY for a country or a region
    # before the country data is loaded; 404 for a slot the team does not
    # have. On the desktop the board is held: the revision is the last
    # published one.
    _player_slot(n, t, p)
    body = _json_body()
    if not body:
        raise ApiError(
            INVALID_ARGUMENT,
            "one of "
            f"{', '.join((*_PLAYER_FIELD_KEYS, *_PLAYER_LOCATION_FIELDS))} "
            "is required", 400)
    texts = {}
    location = {}
    for key, value in body.items():
        if key not in _PLAYER_FIELD_KEYS and key not in _PLAYER_LOCATION_FIELDS:
            raise ApiError(
                INVALID_ARGUMENT, f"unknown player field: {key}", 400)
        if not isinstance(value, str):
            raise ApiError(INVALID_ARGUMENT, f"{key} must be a string", 400)
        if key in _PLAYER_FIELD_KEYS:
            _player_text_rule(key, value)
            texts[_PLAYER_FIELD_KEYS[key]] = value
        else:
            location[key] = value
    if any(location.values()) and not CountryCatalog.instance.IsLoaded():
        raise ApiError(NOT_READY, "country data is not loaded yet", 409)
    try:
        refused = WebOps.update_player_fields(
            n, t, p, texts,
            location.get("country_code", PlayerService.NOT_SENT),
            location.get("state_code", PlayerService.NOT_SENT))
    except WebOps.NoSuchSlot:
        raise ApiError(NOT_FOUND, f"team {t} has no player {p}", 404)
    # No value in the messages: the route logs keys only (player data), as
    # PUT /players' messages carry none.
    if refused == PlayerService.UNKNOWN_COUNTRY:
        raise ApiError(INVALID_ARGUMENT, "unknown country_code", 400)
    if refused == PlayerService.UNKNOWN_REGION:
        raise ApiError(
            INVALID_ARGUMENT, "state_code is not a region of the country", 400)
    return ok(revision=current_revision())


_PLAYER_CHARACTER_FIELDS = ("en_name", "skin", "variant", "game")


@api_v1.route(
    "/scoreboards/<int:n>/teams/<int:t>/players/<int:p>/characters/<int:row>",
    methods=["PATCH"])
@write_op("player.character")
def player_character_update(n, t, p, row):
    # Some of {"en_name": a character of GET /characters ("" empties the
    # row), "skin": its row in GET /characters' skins (an integer from 0;
    # any for a character without skins), "variant": a variant of GET
    # /variants ("" or null: none), "game": the codename of the game whose
    # list it was picked on (optional)}: character row `row` (1-based) of the
    # slot of state team `t`, as the desktop card's three combos pick it
    # (PlayerService.EditCharacter; ADR-005 S4z). Another character takes
    # its first skin (none without skins) unless a skin is sent; the variant
    # stays unless one is sent. Nothing is written when a field is refused:
    # 400 for an unknown key, a value of another type, a character, a skin
    # or a variant the loaded game does not have; 404 for a slot the team
    # does not have, or a row the slot does not have; 409 CONFLICT for a
    # "game" that is not the game loaded (a list from before a switch). On
    # the desktop the board is held: the revision is the last published one.
    _player_slot(n, t, p)
    if row < 1:
        raise ApiError(INVALID_ARGUMENT, "row must be 1 or greater", 400)
    body = _json_body()
    unknown = sorted(k for k in body if k not in _PLAYER_CHARACTER_FIELDS)
    if unknown:
        raise ApiError(
            INVALID_ARGUMENT, f"unknown character field: {', '.join(unknown)}",
            400)
    if not set(body) & set(_PLAYER_CHARACTER_FIELDS[:3]):
        raise ApiError(
            INVALID_ARGUMENT,
            f"one of {', '.join(_PLAYER_CHARACTER_FIELDS[:3])} is required",
            400)
    picks = {}
    if "en_name" in body:
        if not isinstance(body["en_name"], str):
            raise ApiError(INVALID_ARGUMENT, "en_name must be a string", 400)
        picks["character"] = body["en_name"]
    if "skin" in body:
        if not _is_index(body["skin"]) or body["skin"] < 0:
            raise ApiError(
                INVALID_ARGUMENT, "skin must be an integer from 0", 400)
        picks["skin"] = body["skin"]
    if "variant" in body:
        if body["variant"] is not None and not isinstance(body["variant"], str):
            raise ApiError(
                INVALID_ARGUMENT, "variant must be a string or null", 400)
        picks["variant"] = body["variant"]
    game = body.get("game")
    if "game" in body and not isinstance(game, str):
        raise ApiError(INVALID_ARGUMENT, "game must be a string", 400)
    try:
        refused = WebOps.update_player_character(
            n, t, p, row, game=game, **picks)
    except WebOps.NoSuchSlot:
        raise ApiError(NOT_FOUND, f"team {t} has no player {p}", 404)
    if refused == PlayerService.NO_SUCH_ROW:
        raise ApiError(
            NOT_FOUND, f"player {p} has no character row {row}", 404)
    if refused == PlayerService.UNKNOWN_CHARACTER:
        raise ApiError(
            INVALID_ARGUMENT,
            f"no character {body['en_name']} in the loaded game", 400)
    if refused == PlayerService.UNKNOWN_SKIN:
        raise ApiError(INVALID_ARGUMENT, f"no skin {body['skin']} for that "
                       "character", 400)
    if refused == PlayerService.UNKNOWN_VARIANT:
        raise ApiError(
            INVALID_ARGUMENT,
            f"no variant {body['variant']} in the loaded game", 400)
    if refused == PlayerService.OTHER_GAME:
        raise ApiError(
            CONFLICT,
            f"the character was picked for the game {game!r}, not the "
            "loaded one", 409)
    return ok(revision=current_revision())


@api_v1.route(
    "/scoreboards/<int:n>/teams/<int:t>/players/<int:p>:load", methods=["POST"])
@write_op("player.load", log_keys_only=True)
def player_load(n, t, p):
    # {"tag"} or {"key"}: load a player of the local database into the slot,
    # clearing it first (ADR-005 S4m; the legacy -load-player-from-tag-
    # route's operation, and the player list's "set to P1 / P2"). `tag` is a
    # gamer tag (ignoring case; no sponsor): the first record that has it.
    # `key` is a record's own key (GET /players' `key`: the sponsor and the
    # tag, PlayerDB.PlayerTag), matched exactly: that record (ADR-005 S4z),
    # whatever `tag` says besides. Unlike PATCH players, `t` is the set's
    # team: after a swap team 1 lands on state team 2, as the legacy
    # route's. An unknown tag or key, or a slot the team does not have: 404,
    # with no tag in the message.
    _player_slot(n, t, p)
    body = _json_body()
    if "key" in body:
        key = body["key"]
        if not isinstance(key, str) or not key:
            raise ApiError(
                INVALID_ARGUMENT, "key must be a non-empty string", 400)
        load, missing = (lambda: WebOps.load_player_from_key(n, key, t, p),
                         "no player with that key")
    else:
        tag = body.get("tag")
        if not isinstance(tag, str) or not tag:
            raise ApiError(
                INVALID_ARGUMENT, "tag must be a non-empty string", 400)
        load, missing = (lambda: WebOps.load_player_from_tag(n, tag, t, p),
                         "no player with that tag")
    try:
        result = load()
    except WebOps.NoSuchSlot:
        raise ApiError(NOT_FOUND, f"team {t} has no player {p}", 404)
    if result != "OK":
        raise ApiError(NOT_FOUND, missing, 404)
    return ok(revision=current_revision())


@api_v1.route(
    "/scoreboards/<int:n>/teams/<int:t>/players/<int:p>:clear", methods=["POST"])
@write_op("player.clear")
def player_clear(n, t, p):
    # {"confirm": true}: empty the slot of state team `t`, as the card's clear
    # button does (ADR-005 S4u). 404 when the team has no such slot.
    require_board_12(n)
    _validate_team(t)
    if p < 1:
        raise ApiError(INVALID_ARGUMENT, "player must be 1 or greater", 400)
    _require_confirm(_json_body())
    if not WebOps.clear_player(n, t, p):
        raise ApiError(NOT_FOUND, f"team {t} has no player {p}", 404)
    return ok(revision=current_revision())


@api_v1.route(
    "/scoreboards/<int:n>/teams/<int:t>/players:swap", methods=["POST"])
@write_op("player.swap")
def player_swap(n, t):
    # {"first", "second"}: exchange two slots of state team `t`, as a card's
    # move up / down does (ADR-005 S4u). Two different slots, both of which
    # the team has (404 otherwise: nothing moves).
    require_board_12(n)
    _validate_team(t)
    body = _json_body()
    slots = [body.get("first"), body.get("second")]
    if any(isinstance(slot, bool) or not isinstance(slot, int) or slot < 1
           for slot in slots):
        raise ApiError(
            INVALID_ARGUMENT, "first and second must be 1 or greater", 400)
    if slots[0] == slots[1]:
        raise ApiError(
            INVALID_ARGUMENT, "first and second must be different slots", 400)
    if not WebOps.swap_players(n, t, slots[0], slots[1]):
        raise ApiError(
            NOT_FOUND, f"team {t} does not have both players", 404)
    return ok(revision=current_revision())


@api_v1.route(
    "/scoreboards/<int:n>/teams/<int:t>/members/<int:m>", methods=["PATCH"])
@write_op("crew.member.update", log_keys_only=True)
def crew_member_update(n, t, m):
    # Some of {"name": a string ("" empties the slot), "stock": 0..99 (the
    # board's spinbox), "active": true (this member, one the board shows, is
    # playing, in place of whoever was) or false (this member no longer is:
    # a member the state has moved on from leaves the marker where it is)},
    # in one save cycle: the
    # crew board's member row (ADR-005 S4w). The state team, as every v1 team
    # number; a swap moves the crews, not the columns. A name is logged by key
    # only, as the player PATCH's are.
    _crew_member(n, t, m)
    body = _json_body()
    if not body:
        raise ApiError(
            INVALID_ARGUMENT, "one of name, stock, active is required", 400)
    fields = {}
    for key, value in body.items():
        if key == "name":
            if not isinstance(value, str):
                raise ApiError(INVALID_ARGUMENT, "name must be a string", 400)
        elif key == "stock":
            value = _crew_int(body, key, 0, CrewBattleService.STOCK_MAX)
        elif key == "active":
            if not isinstance(value, bool):
                raise ApiError(
                    INVALID_ARGUMENT, "active must be true or false", 400)
        else:
            raise ApiError(
                INVALID_ARGUMENT, f"unknown member field: {key}", 400)
        fields[key] = value
    # Only a member the board shows can be marked as playing: Qt hides the
    # rows past the count, radio button and all. The rest (a name, a stock,
    # `active: false` for a marker a lowered count left behind) goes to any
    # row. Read before the write, not under its lock: a count lowered in
    # between leaves what marking first and lowering after leaves (the two
    # writes commute), as Qt can.
    if fields.get("active") is True:
        shown = WebOps.crew_member_count()
        if m > shown:
            raise ApiError(
                INVALID_ARGUMENT,
                f"member {m} is not shown (the crew has {shown}): only a "
                "shown member can be playing", 400)
    WebOps.crew_update_member(t, m, fields)
    return ok(revision=current_revision())


@api_v1.route(
    "/scoreboards/<int:n>/teams/<int:t>/members/<int:m>/stock:adjust",
    methods=["POST"])
@write_op("crew.member.stock")
def crew_member_stock_adjust(n, t, m):
    # {"delta"}: take stocks from / give them to one member, kept to 0..99
    # (ADR-005 S4w). A relative operation, so an Idempotency-Key is applied
    # once, as score:adjust's (spec §2.9). 0 changes nothing, and builds nothing.
    _crew_member(n, t, m)
    body = _json_body()
    delta = body.get("delta")
    if not _is_index(delta):
        raise ApiError(INVALID_ARGUMENT, "delta must be an integer", 400)

    def apply():
        if delta != 0:
            WebOps.crew_member_stock(n, t, m, delta)

    key = request.headers.get("Idempotency-Key")
    if key:
        if _idem.claim(key):
            try:
                apply()
            except Exception:
                _idem.release(key)
                raise
    else:
        apply()
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

# A player's texts (PUT /players, PATCH .../fields). Far over any real value:
# they keep what a client sends out of the file and of every row that reads
# it, not a name out of the file.
_PLAYER_LINE_MAX = 256
_PLAYER_NOTE_MAX = 4096


def _player_text_rule(key, value):
    """400 for a player's text that breaks the rule PUT /players and PATCH
    .../fields share (ADR-005 S4z review): the note (custom_textbox) has
    lines; the others are one line -- no line break anywhere -- and each is
    at most its length. No value in the messages: a refused one can be a
    person's."""
    if key == "custom_textbox":
        if len(value) > _PLAYER_NOTE_MAX:
            raise ApiError(
                INVALID_ARGUMENT,
                f"{key} must be at most {_PLAYER_NOTE_MAX} characters", 400)
        return
    if "".join(value.splitlines()) != value:
        raise ApiError(INVALID_ARGUMENT, f"{key} must be one line", 400)
    if len(value) > _PLAYER_LINE_MAX:
        raise ApiError(
            INVALID_ARGUMENT,
            f"{key} must be at most {_PLAYER_LINE_MAX} characters", 400)


def _player_save_text(key, value):
    """A text of PUT /players as it is saved: a one-line text is trimmed (a
    space pasted around a tag would be another database key), then held to
    _player_text_rule."""
    if key != "custom_textbox":
        value = value.strip()
    _player_text_rule(key, value)
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


@api_v1.route("/layouts/selected/board:reveal", methods=["POST"])
@write_op("layout.board.reveal")
def reveal_board_file():
    # {"target": "design" | "winnerPopup"}, and for "design" {"designId": the
    # design the client shows}: shows that file in this PC's file manager
    # (ADR-005 S4s). The path is the catalog's: a client names a target, never a
    # path. WebAccess.THIS_PC: a window is not to be opened on the streaming PC's
    # screen from another computer, token or not. 404 when the file is not there,
    # 409 when another design is selected by now: not the file the operator saw.
    body = _json_body()
    target = body.get("target")
    if not isinstance(target, str) or target not in LayoutCatalog.BOARD_FILES:
        raise ApiError(
            INVALID_ARGUMENT,
            f"target must be one of: {', '.join(LayoutCatalog.BOARD_FILES)}",
            400,
        )
    designId = None
    if target == LayoutCatalog.BOARD_FILE_DESIGN:
        designId = body.get("designId")
        if not isinstance(designId, str) or not designId:
            raise ApiError(INVALID_ARGUMENT,
                           "designId must name the design shown", 400)
    try:
        shown = WebOps.reveal_board_file(target, designId)
    except LayoutCatalog.SelectionChanged:
        raise ApiError(CONFLICT, "another design is selected by now", 409)
    if not shown:
        raise ApiError(NOT_FOUND, f"no {target} file to show", 404)
    return ok()


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


# ── 設定 (ADR-005 S4ae) ──
# The settings page's saves: core's AppSettings / HotkeyRules validate every
# field first and write once (nothing when nothing changes, nothing at all
# when settings.json could not be read at launch and still cannot be opened:
# 409 NOT_READY; SettingsManager.PrepareWrite). Each answers its
# section of GET /settings as it now is. Settings, not state: no revision.
# general is WRITE; network, language and hotkeys THIS_PC (WebAccess).

def _settings_refused(refusal):
    """The ApiError for a refusal of AppSettings / HotkeyRules."""
    if refusal.code == AppSettings.NOT_READY:
        raise ApiError(
            NOT_READY, "settings.json could not be read: nothing was saved",
            409)
    if refusal.code == HotkeyRules.CONFLICT:
        raise ApiError(
            CONFLICT,
            f"{refusal.name}: the shortcut is already assigned to: "
            f"{refusal.detail}", 409)
    message = (f"{refusal.name} {refusal.detail}" if refusal.name
               else refusal.detail)
    raise ApiError(INVALID_ARGUMENT, message, 400)


@api_v1.route("/settings/general", methods=["PATCH"])
@write_op("settings.general")
def settings_general_update():
    # Some of profanity_filter, disable_autoupdate, disable_scoreupdate,
    # disable_export, disable_overwrite (true / false) and
    # team_1_default_color, team_2_default_color (#rgb / #rrggbb, saved as
    # #rrggbb).
    refusal, _written = WebOps.update_general_settings(_json_body())
    if refusal is not None:
        _settings_refused(refusal)
    return ok(data=WebOps.get_settings()["general"])


@api_v1.route("/settings/network", methods=["PATCH"])
@write_op("settings.network")
def settings_network_update():
    # Some of {"port": 1..65535, "listenLan": true / false}: applied by a
    # restart (restartPending says whether one is still to come).
    refusal, _written = WebOps.update_network_settings(_json_body())
    if refusal is not None:
        _settings_refused(refusal)
    return ok(data=WebOps.get_settings()["network"])


@api_v1.route("/settings/language", methods=["PATCH"])
@write_op("settings.language")
def settings_language_update():
    # Some of {"program", "gameAsset", "fgTerm"}: "default" or a code of
    # `choices`. Applied by a restart; the program keeps running.
    refusal, _written = WebOps.update_language_settings(_json_body())
    if refusal is not None:
        _settings_refused(refusal)
    return ok(data=WebOps.get_settings()["language"])


@api_v1.route("/settings/hotkeys", methods=["PATCH"])
@write_op("settings.hotkeys")
def settings_hotkeys_update():
    # Some of {"enabled": true / false, "keys": {<action>: <shortcut>}}: a
    # shortcut is Ctrl / Alt / Shift and one key of the vocabulary (saved as
    # "Ctrl+Alt+Shift+Key"), "" for none. A shortcut another action has
    # (after this request) is 409 CONFLICT naming it, and nothing is saved.
    # One that clashes with an OS / OBS binding, has no modifier or Shift
    # alone (function keys excepted) is saved, and named in data.warnings
    # ([{action, code: "os_reserved" | "obs_likely" | "no_modifier" |
    # "shift_only"}]).
    body = _json_body()
    unknown = sorted(key for key in body if key not in ("enabled", "keys"))
    if unknown:
        raise ApiError(
            INVALID_ARGUMENT,
            f"unknown hotkey setting: {', '.join(unknown)} (enabled, keys)",
            400)
    if not body:
        raise ApiError(
            INVALID_ARGUMENT, "at least one of enabled, keys is required",
            400)
    # null is not "leave it": core reads None as not sent.
    if "enabled" in body and not isinstance(body["enabled"], bool):
        raise ApiError(INVALID_ARGUMENT, "enabled must be true or false", 400)
    if "keys" in body and not isinstance(body["keys"], dict):
        raise ApiError(
            INVALID_ARGUMENT, "keys must be an object of shortcuts", 400)
    refusal, warnings, _written = WebOps.update_hotkey_settings(
        enabled=body.get("enabled"), keys=body.get("keys"))
    if refusal is not None:
        _settings_refused(refusal)
    return ok(data=dict(WebOps.get_settings()["hotkeys"], warnings=warnings))


# ── The guides (ADR-005 S-final SF-3a) ───────────────────────────────────
# What the API / Companion / OBS guide pages read (SF-4a-2: /web/api,
# /web/companion, /web/obs; the Qt windows read it for themselves until
# then). The connection is WebAccess.THIS_PC: refused (403, token or not)
# to anything but a request from this PC that
# passes without a token -- it names this PC's folders. The access token is
# WebAccess.SHELL (SF-4a-1): a page of this origin (any layout/ page, in OBS
# or a browser) passes as this PC's own request too, so it also takes the
# secret the shell adds to the shared view's own fetches.

def _no_store(resp):
    # This PC's paths: kept out of the browser's caches.
    resp.headers["Cache-Control"] = "no-store"
    return resp


@api_v1.route("/api-catalog")
@api_handler
def get_api_catalog():
    # {"endpoints": [{id, method, path, example, description, body,
    # destructive, logged, confirm}], "errorCodes": [{code, status,
    # description}]}: the API page's reference (core's ApiCatalog). `id` is
    # the row's route (its Flask endpoint name, stable: what a page keys its
    # translations by); `description` the English text (the page's fallback).
    # Nothing but this module's own routes, so READ.
    return ok(data=WebOps.get_api_catalog())


@api_v1.route("/connection")
@api_handler
def get_connection():
    # {"port", "lan", "lanAtRestart", "baseUrl", "localBaseUrl", "outDir",
    # "pathSeparator"}: what the guide pages show of the server and its
    # files (AutonaWebOperations.get_connection). THIS_PC: out/'s absolute
    # path names this PC's folders.
    return _no_store(ok(data=WebOps.get_connection()))


@api_v1.route("/access-token")
@api_handler
def get_access_token():
    # {"token", "available"}: the token another computer's requests carry
    # (WebAccess.Token(); "" and available false when there is none to use
    # -- settings.json unread, or a malformed one). SHELL: only the shared
    # view's own fetches, never a LAN device, never with the token.
    token = WebOps.get_access_token()
    return _no_store(ok(data={"token": token, "available": bool(token)}))


@api_v1.route("/access-token:regenerate", methods=["POST"])
@write_op("access-token.regenerate", log_keys_only=True)
def access_token_regenerate():
    # {"confirm": true}: replace the token (WebAccess.RegenerateToken); the
    # old one stops working at once, a Socket.IO connection it let in at its
    # next event. 409 NOT_READY when none was issued (settings.json still
    # cannot be opened): the old one is still the one. The op log keeps the
    # body's keys only, and the answer is not cached.
    _require_confirm(_json_body())
    token, issued = WebOps.regenerate_access_token()
    if not issued:
        raise ApiError(
            NOT_READY,
            "settings.json could not be read: no new token was issued", 409)
    return _no_store(ok(data={"token": token, "available": bool(token)}))


@api_v1.route("/shell-session")
@api_handler
def get_shell_session():
    # {"ticket"}: what the shared view's page puts in a shell request (its
    # window.open of /web/shell-request in a click; ADR-005 S-final SF-5a-1;
    # AutonaWebPanelHost.ParseShellRequest compares it). SHELL: only the
    # shared view's own fetches -- never a LAN device, never with the token,
    # never the headless app. Not cached, and not in core's ApiCatalog: it is
    # not for a person to call.
    return _no_store(ok(data={"ticket": WebOps.get_shell_session()}))


# ── Game asset packs (ADR-005 S-final SF-3b / SF-4b-1) ──
# What the "Add game assets" window shows and does, for the page that
# replaces it (SF-4b): core's AssetPackService. The list is THIS_PC (it may
# fetch from GitHub). Everything else is WebAccess.SHELL (SF-4b-1): the last
# job's state (a job's messages may name this PC's paths), the ZIP upload,
# the jobs that write user_data/games (an install, the uploaded ZIP's
# import) and their cancel, and the games folder's reveal -- THIS_PC also
# lets the layout/ pages of this origin through, which must not replace
# installed games, download gigabytes or fill the disk. The secret is the
# shell's, handed only to the shared view's main frame: headless answers
# them all 403. A folder import is not here (a page cannot name a folder;
# SF-5a's picker brings it back). Every answer is no-store, and a refusal
# answers core's code (error.reason) with the words of _ASSET_REFUSALS --
# core's message, which may name a path or the archive's entries, goes to
# the log alone. Not listed in the API catalog (the shared view's page
# alone may call them).

@api_v1.route("/assets/packs")
@api_handler
def get_asset_packs():
    # {"source": {"page", "index"}, "index": {"status", "error",
    # "fetchedAt"}, "games": [{"code", "name", "available", "installed",
    # "packs": [{"name", "size", "version", "installedVersion",
    # "installed", "updateAvailable", "default"}]}]}
    # (AssetPackService.Packs). The index is fetched on this
    # thread when it is not cached or older than 10 minutes, or
    # `?refresh=true` (at most once every 5 seconds); a failed fetch is
    # still 200, index.status "error", with the installed games.
    refresh = _truthy(request.args.get("refresh", ""))
    return ok(data=WebOps.get_asset_packs(refresh=refresh))


@api_v1.route("/assets/job")
@api_handler
def get_assets_job():
    # The last asset job's state: {"revision", "status" (idle | running |
    # done | error | cancelled), "operation", "game", "stage" (download |
    # extract | copy | scan), "pack", "file", "done", "total" (bytes for a
    # download, entries / files for an import), "imported", "skipped",
    # "skippedEntries", "skippedEntriesMore", "failed": [{"name",
    # "message"}], "error" (core's reason), "message"}.
    # SHELL (SF-4b-1; THIS_PC until then): a message may name this PC's
    # paths, and the shared view's page is its one reader.
    return _no_store(ok(data=WebOps.get_assets_job()))


# Core's refusal codes (AssetPackService.AssetPackError.code, and BUSY) ->
# (code, fixed words, status). The answer's error.reason is core's code.
_ASSET_REFUSALS = {
    assets.UNKNOWN_GAME: (NOT_FOUND, "no such game in the asset index", 404),
    assets.UNKNOWN_PACK: (INVALID_ARGUMENT,
                          "not an offered pack of that game", 400),
    assets.NO_PACKS: (INVALID_ARGUMENT, "select at least one pack", 400),
    assets.INDEX_UNAVAILABLE: (
        NOT_READY, "the asset index is not loaded: list the packs first",
        409),
    assets.BAD_INDEX: (INTERNAL, "the asset index could not be used", 500),
    assets.NOT_A_ZIP: (INVALID_ARGUMENT, "not a readable ZIP", 400),
    assets.NOT_A_FOLDER: (INVALID_ARGUMENT, "not a folder", 400),
    assets.UNSAFE_ARCHIVE: (
        INVALID_ARGUMENT,
        "the ZIP has an entry that would be written outside its folder", 400),
    assets.TOO_LARGE: (PAYLOAD_TOO_LARGE, "the ZIP is too large", 413),
    assets.NO_GAME: (
        INVALID_ARGUMENT,
        "no game found: a game folder must contain base_files/config.json",
        400),
    assets.BAD_NAME: (INVALID_ARGUMENT,
                      "a game's folder name cannot be used", 400),
    assets.INCOMPLETE: (INVALID_ARGUMENT,
                        "the upload did not arrive whole", 400),
    assets.SUPERSEDED: (CONFLICT, "a newer upload replaced this one", 409),
    assets.DISK_FULL: (NOT_READY, "not enough free space on this PC", 409),
    assets.UNKNOWN_UPLOAD: (
        NOT_FOUND,
        "no such upload (imported, dropped, replaced or expired)", 404),
    assets.BUSY: (CONFLICT, "another asset job is running", 409),
    assets.STOPPED: (UNAVAILABLE, "the app is shutting down", 503),
    assets.FAILED: (INTERNAL, "the asset operation failed", 500),
}
_ASSET_REFUSAL_OTHER = (INTERNAL, "the asset operation failed", 500)


def _asset_refused(operation, code, message=None):
    """The ApiError for core's refusal ``code``: _ASSET_REFUSALS' words, the
    code as error.reason. Core's ``message`` is logged here, never answered
    (it may name a path or the archive's entries)."""
    if message is not None:
        logger.info(f"API v1: assets {operation} refused: {code}: {message}")
    api, words, status = _ASSET_REFUSALS.get(code, _ASSET_REFUSAL_OTHER)
    return ApiError(api, words, status, reason=code)


# The client's file name (X-Autona-File-Name, percent-encoded UTF-8: a
# header carries latin-1 alone): it names a game at the ZIP's top.
ASSET_FILE_NAME_HEADER = "X-Autona-File-Name"
_ASSET_FILE_NAME_MAX = 255


def _asset_file_name():
    raw = request.headers.get(ASSET_FILE_NAME_HEADER)
    if raw is None or raw == "":
        return None
    try:
        name = unquote(raw, errors="strict")
    except UnicodeDecodeError:
        name = None
    if (not name or len(name) > _ASSET_FILE_NAME_MAX
            or any(ord(c) < 32 or ord(c) == 127 for c in name)):
        raise ApiError(
            INVALID_ARGUMENT, f"{ASSET_FILE_NAME_HEADER} must be the file's "
            "name, percent-encoded UTF-8", 400)
    return name


def _asset_job_started(operation, answer):
    # A job's start: (STARTED, revision) -> 202 with its revision in
    # data.job (GET /assets/job follows it), or BUSY -> 409 reason "busy".
    started, revision = answer
    if started == assets.BUSY:
        raise _asset_refused(operation, assets.BUSY)
    return ok(data={"accepted": True, "operation": operation,
                    "job": revision}, status=202)


def _no_store_answers(fn):
    # Every answer of the route no-store, its refusals included (outside
    # write_op, which turns them into envelopes).
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return _no_store(fn(*args, **kwargs))

    return wrapper


@api_v1.route("/assets/uploads", methods=["POST"])
@_no_store_answers
@write_op("assets.upload", body=BODY_NONE)
def upload_assets_zip():
    # The ZIP's bytes as the body (not multipart), with a Content-Length
    # (400 without: chunked is refused) of at most MAX_UPLOAD_BYTES (413
    # over): both answered without this reading a byte (werkzeug then reads
    # the rest and drops it: write_op's docstring). X-Autona-File-Name: the
    # file's name, percent-encoded (it names a game at the ZIP's top; never
    # logged). Core reads it to a file outside the served folders, probes it
    # and holds it (one at a time, a new one replacing it; for
    # UPLOAD_EXPIRY_SECS) -> 201 {"upload": its id, "games": the games it
    # holds, "conflicts": the installed ones among them, "expiresInSec"}.
    # The path is never answered.
    request.max_content_length = assets.MAX_UPLOAD_BYTES
    length = request.content_length
    if length is None:
        raise ApiError(
            INVALID_ARGUMENT, "Content-Length is required: send the ZIP's "
            "bytes as the body, not chunked", 400)
    if length > assets.MAX_UPLOAD_BYTES:
        raise ApiError(
            PAYLOAD_TOO_LARGE,
            f"an upload is at most {assets.MAX_UPLOAD_BYTES} bytes", 413,
            reason=assets.TOO_LARGE)
    fileName = _asset_file_name()
    try:
        uploadId, games, conflicts = WebOps.upload_assets_zip(
            request.stream, length, fileName)
    except assets.AssetPackError as error:
        raise _asset_refused("upload", error.code, error.message)
    return ok(data={"upload": uploadId, "games": games,
                    "conflicts": conflicts,
                    "expiresInSec": assets.UPLOAD_EXPIRY_SECS}, status=201)


@api_v1.route("/assets/uploads/<upload_id>", methods=["DELETE"])
@_no_store_answers
@write_op("assets.upload.drop", body=BODY_NONE)
def drop_asset_upload(upload_id):
    # Remove the upload held by that id -> {"removed": whether one was}
    # (not a 404: imported, replaced or expired is gone all the same).
    return ok(data={"removed": bool(WebOps.drop_asset_upload(upload_id))})


@api_v1.route("/assets:importZip", methods=["POST"])
@_no_store_answers
@write_op("assets.importZip", log_keys_only=True)
def import_assets_zip():
    # {"upload": POST /assets/uploads's id, "overwrite": [the installed
    # games to replace -- each one of the upload's conflicts, else 400],
    # "confirm": true when overwrite names any}: imports the upload (core
    # takes it out: the job removes it however it ends) -> 202 {"accepted",
    # "operation": "importZip", "job": its revision}. 404 reason
    # unknown_upload; 409 reason busy while a job runs (the upload stays).
    # Logged by key: the body names the upload.
    body = _json_body()
    uploadId = body.get("upload")
    if not isinstance(uploadId, str) or not uploadId:
        raise ApiError(INVALID_ARGUMENT,
                       "upload must be POST /assets/uploads's data.upload",
                       400)
    overwrite = body.get("overwrite")
    if overwrite is None:
        overwrite = []
    if not isinstance(overwrite, list) or not all(
            isinstance(name, str) and name for name in overwrite):
        raise ApiError(INVALID_ARGUMENT,
                       "overwrite must be a list of game names", 400)
    try:
        _games, conflicts = WebOps.asset_upload_games(uploadId)
    except assets.AssetPackError as error:
        raise _asset_refused("importZip", error.code, error.message)
    if any(name not in conflicts for name in overwrite):
        raise ApiError(
            INVALID_ARGUMENT, "overwrite may name only the upload's "
            "conflicts (the installed games it would replace)", 400)
    if overwrite:
        _require_confirm(body)
    try:
        answer = WebOps.import_asset_upload(
            uploadId, list(dict.fromkeys(overwrite)))
    except assets.AssetPackError as error:
        raise _asset_refused("importZip", error.code, error.message)
    return _asset_job_started(assets.IMPORT_ZIP, answer)


@api_v1.route("/assets/packs:install", methods=["POST"])
@_no_store_answers
@write_op("assets.packs.install")
def install_asset_packs():
    # {"game": an index game's code, "packs": [allowed pack names] (absent
    # or null: the default ones it has), "confirm": true (always: it
    # replaces the game's files and downloads up to gigabytes)} -> 202
    # {"accepted", "operation": "install", "job": its revision}. The cached
    # index (GET /assets/packs loads it): 409 NOT_READY reason
    # index_unavailable when there is none; this write never fetches.
    body = _json_body()
    game = body.get("game")
    if not isinstance(game, str) or not game:
        raise ApiError(INVALID_ARGUMENT, "game must be an index game's code",
                       400)
    packs = body.get("packs")
    if packs is not None and (not isinstance(packs, list) or not all(
            isinstance(name, str) for name in packs)):
        raise ApiError(INVALID_ARGUMENT, "packs must be a list of pack names",
                       400)
    _require_confirm(body)
    try:
        answer = WebOps.install_asset_packs(game, packs)
    except assets.AssetPackError as error:
        raise _asset_refused("install", error.code, error.message)
    return _asset_job_started(assets.INSTALL, answer)


@api_v1.route("/assets/job:cancel", methods=["POST"])
@_no_store_answers
@write_op("assets.job.cancel")
def cancel_assets_job():
    # {"revision": the job's (GET /assets/job / the start's data.job)}: asks
    # that job to stop -> {"cancelled": false when it is not running (done,
    # or another job runs)}. Seen at the job's next step: GET /assets/job
    # tells when it has stopped.
    body = _json_body()
    revision = body.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or \
            revision < 1:
        raise ApiError(INVALID_ARGUMENT,
                       "revision must be the job's revision", 400)
    return ok(data={"cancelled": bool(WebOps.cancel_assets_job(revision))})


@api_v1.route("/assets/games-folder:reveal", methods=["POST"])
@_no_store_answers
@write_op("assets.gamesFolder.reveal")
def reveal_games_folder():
    # Opens user_data/games in this PC's file manager (made first). Takes
    # no path: the folder is core's.
    if not WebOps.reveal_games_folder():
        raise ApiError(INTERNAL, "the games folder could not be shown", 500)
    return ok()


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
    # 2999-12-31, ASCII digits), "displayTournamentName": str}:
    # tournamentInfo.* through TournamentInfoService, in one save cycle. The
    # date is published as tournamentInfo.startAt (the PC's short date, as
    # before) and startAtDate; it cannot be emptied (the Qt tab's date edit
    # could not either). A day this PC cannot make a time of is refused too,
    # with nothing written (S4g review L4). A payload loaded later replaces
    # them, as it did. displayTournamentName is the 表示大会名 of the SCORE
    # panel (ADR-005 S4u; not an EVENT field, so not in TOURNAMENT_INPUT_KEYS).
    # An edit of the tournament name carries into it only while it is empty or
    # still the previous tournament name (FollowDisplayName); selecting a
    # tournament again overwrites it whatever it is.
    body = _json_body()
    fields = schema.TOURNAMENT_INPUT_KEYS + (schema.TOURNAMENT_DISPLAY_NAME_KEY,)
    unknown = sorted(k for k in body if k not in fields)
    if unknown:
        raise ApiError(
            INVALID_ARGUMENT,
            f"unknown tournament field: {', '.join(unknown)}", 400)
    if not body:
        raise ApiError(
            INVALID_ARGUMENT, f"one of {', '.join(fields)} is required", 400)
    for key, value in body.items():
        if (key in schema.TOURNAMENT_TEXT_FIELDS
                or key == schema.TOURNAMENT_DISPLAY_NAME_KEY):
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
    # Board 3: the reset of :reset-match, one member a crew, every row emptied
    # (the crews' names and colours stay).
    validate_scoreboard(n)
    _require_confirm(_json_body())
    WebOps.clear_all(n)
    return ok(revision=current_revision())


@api_v1.route("/scoreboards/<int:n>:reset-all", methods=["POST"])
@write_op("reset-all")
def reset_all(n):
    # The desktop's すべての入力値をリセット (AutonaActions.ClearBoard, ADR-005
    # S4u). Not :clear, which puts the counts back to one and keeps the team
    # names: this empties the team names too and leaves the counts.
    require_board_12(n)
    _require_confirm(_json_body())
    WebOps.reset_all(n)
    return ok(revision=current_revision())


@api_v1.route("/scoreboards/<int:n>:reset-players", methods=["POST"])
@write_op("reset-players")
def reset_players(n):
    # Board 3: every member row emptied, the count stays (ClearCrew).
    validate_scoreboard(n)
    _require_confirm(_json_body())
    WebOps.reset_players(n)
    return ok(revision=current_revision())


@api_v1.route("/scoreboards/<int:n>:reset-match", methods=["POST"])
@write_op("reset-match")
def reset_match(n):
    # Board 3: full stocks and nobody playing, the match bar and the best of.
    validate_scoreboard(n)
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


def _is_follow_value(value):
    # What start.gg names a station, a stream or a phase by: a positive number
    # or a string. A bool is neither, though Python counts it as a number.
    return (_is_index(value) and value > 0) or (
        isinstance(value, str) and bool(value))


@api_v1.route("/scoreboards/<int:n>:follow", methods=["POST"])
@write_op("follow")
def follow(n):
    # A row of GET /tournament/stations: {"type": "station", "id", "identifier"
    # (optional: what the board shows it as)} or {"type": "stream",
    # "identifier": the channel, "id" (optional)}. As the station window's row
    # (ADR-005 S4v; SetFollowService.FollowStation): its current set is looked
    # up and loaded, whatever the board shows -- a set it has none of leaves the
    # board as it is. It is not checked against the list: the board follows
    # what it is told, as :pull-stream follows the setting's channel. With no
    # tournament there is nothing to follow.
    require_board_12(n)
    body = _json_body()
    kind = body.get("type")
    if kind not in ("station", "stream"):
        raise ApiError(
            INVALID_ARGUMENT, 'type must be "station" or "stream"', 400)
    stationId, identifier = body.get("id"), body.get("identifier")
    if kind == "station" and stationId is None:
        raise ApiError(INVALID_ARGUMENT, "a station needs its id", 400)
    if kind == "stream" and not isinstance(identifier, str):
        raise ApiError(
            INVALID_ARGUMENT, "a stream needs its identifier, a string", 400)
    for name, value in (("id", stationId), ("identifier", identifier)):
        if value is not None and not _is_follow_value(value):
            raise ApiError(INVALID_ARGUMENT,
                           f"{name} must be a number or a string", 400)
    if AutonaTournamentDataProvider.instance.GetProvider() is None:
        raise ApiError(NOT_READY, "no tournament loaded", 409)
    WebOps.follow_station(
        n, {"type": kind, "id": stationId, "identifier": identifier})
    return ok(revision=current_revision())


@api_v1.route("/scoreboards/<int:n>:stop-follow", methods=["POST"])
@write_op("stop-follow")
def stop_follow(n):
    # The board's timer row's cancel button (ADR-005 S4v): the poll stopped,
    # the station and the followed set forgotten. What the board shows stays.
    require_board_12(n)
    WebOps.stop_follow(n)
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


def _selection_answer(started, operation):
    # What a set selection fetch answered (ADR-005 S4v), by jobs in the
    # background: the list says "running" at the next revision before this
    # answers, and "done" or "error" once it is over.
    if started == selection.BUSY:
        raise ApiError(CONFLICT, "this list is being fetched already", 409)
    if started == selection.NO_TOURNAMENT:
        raise ApiError(NOT_READY, "no tournament loaded", 409)
    return ok(data={"accepted": True, "operation": operation},
              revision=current_revision(), status=202)


@api_v1.route("/tournament/phases:refresh", methods=["POST"])
@write_op("tournament.phases.refresh")
def tournament_phases_refresh():
    return _selection_answer(WebOps.refresh_tournament_phases(),
                             "tournament.phases.refresh")


@api_v1.route("/tournament/stations:refresh", methods=["POST"])
@write_op("tournament.stations.refresh")
def tournament_stations_refresh():
    return _selection_answer(WebOps.refresh_tournament_stations(),
                             "tournament.stations.refresh")


@api_v1.route("/tournament/sets:refresh", methods=["POST"])
@write_op("tournament.sets.refresh")
def tournament_sets_refresh():
    # {"phase": a phase's id | "all" | null (default), "finished": bool
    # (default false)}. While the tournament's phases are known, null is 400
    # (a phase must be chosen; "all" is there for every phase) and an id they
    # do not have 404; with none known the whole event is fetched. The same
    # query while it runs is 409; another one replaces it, as the set window's
    # do (409 too while a stopped one's request is still out).
    body = _json_body()
    phase = body.get("phase")
    if phase is not None and not _is_follow_value(phase):
        raise ApiError(INVALID_ARGUMENT,
                       'phase must be a phase\'s id, "all" or null', 400)
    finished = body.get("finished", False)
    if not isinstance(finished, bool):
        raise ApiError(INVALID_ARGUMENT, "finished must be true or false", 400)
    if AutonaTournamentDataProvider.instance.GetProvider() is None:
        raise ApiError(NOT_READY, "no tournament loaded", 409)
    started = WebOps.refresh_tournament_sets(phase, finished)
    if started == selection.PHASE_REQUIRED:
        raise ApiError(
            INVALID_ARGUMENT, 'choose a phase, or "all" for every one', 400)
    if started == selection.UNKNOWN_PHASE:
        raise ApiError(NOT_FOUND, "the tournament has no such phase", 404)
    return _selection_answer(started, "tournament.sets.refresh")


@api_v1.route("/tournament/sets:cancel", methods=["POST"])
@write_op("tournament.sets.cancel")
def tournament_sets_cancel():
    # Stop the set list's fetch, as choosing a set in the window does: what it
    # still answers is dropped, the rows stay. {"cancelled": whether one ran}.
    return ok(data={"cancelled": WebOps.cancel_tournament_sets()},
              revision=current_revision())


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
    # "done". 409 CONFLICT while one is on its way. {"board": 1 or 2} draws
    # that board's match (ADR-005 S4z; 1 without it, as before). The rest of
    # the body is not read, as it never was. A generate updates the preview
    # image too -- of its board, which is what a kept preview it stands in
    # for shows (ThumbnailService.Generate). Read leniently: a body that is
    # no JSON object was ignored before, and still is (_json_body would
    # refuse it).
    data = request.get_json(force=True, silent=True)
    board = data.get("board", 1) if isinstance(data, dict) else 1
    if not _is_index(board) or board not in (1, 2):
        raise ApiError(INVALID_ARGUMENT, "board must be 1 or 2", 400)
    revision = WebOps.generate_thumbnail(board)
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
