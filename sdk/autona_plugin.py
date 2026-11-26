# MIT License
#
# Copyright (c) 2026 taguyan and Autona contributors
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""autona_plugin -- the official SDK for Autona plugins (Worker Protocol 1,
manifest 1; Autona's ADR-009 and docs/plugins/contract_1.json).

One file, the standard library only (Python 3.8+). Copy it next to your
worker. It is two things:

1. **The worker's helpers** (import it)::

       import autona_plugin as ap

       def post_image(request):
           ap.stage("send", {"ja": "送信", "en": "Send"}, 1, 1)
           url = request.secret("webhook_url")
           image = request.input_path("image")
           ...
           ap.check_cancel()               # raises Cancelled after a cancel
           if bad:
               raise ap.PluginError("unsupported_host",
                                    {"ja": "送れません", "en": "Cannot post"})
           return ap.Done({"ja": "送った", "en": "Posted"},
                          [ap.artifact("message.txt", "text")])

       if __name__ == "__main__":
           ap.main({"post_image": post_image})

   ``main`` answers ``describe`` from ``plugin.json`` (under
   ``AUTONA_PLUGIN_DIR``: the id and version are never typed twice), reads
   ``request.json`` for ``run``, sets stdout to UTF-8 with a flush per line,
   watches stdin for the cancel on a thread (a ``cancel`` line or stdin
   closed: Autona gone), and ends with the protocol's exit code: 0 done, 2
   ``PluginError``, 130 ``Cancelled``, 1 any other exception (its
   traceback on stderr). The event helpers (``stage`` ``progress`` ``log``
   ``warning``, and the terminal ``emit_done`` ``emit_error``
   ``emit_cancelled``) write one JSON line each; print your own diagnostics
   to stderr (Autona keeps them in worker.log). The value of every
   ``AUTONA_SECRET_*`` of 8 characters or more is written as ``***`` in
   the events and in the traceback ``main`` prints.

   The worker's API is ``__all__``; everything else in this file is the
   fake host's.

2. **A fake host** (run it)::

       python autona_plugin.py check <folder> [--dev] [--source manual]
           [--host-version X.Y.Z] [--json]
       python autona_plugin.py run <folder> <job> [--params p.json]
           [--input key=path] [--setting k=v] [--secret k=v]
           [--trigger action:<target>|hook:<event>] [--cancel-after N]
           [--timeout N] [--locale ja|en] [--work DIR] [--json]

   ``check`` does what Autona checks of a plugin folder (the manifest, every
   code of contract_1.json manifest_checks that a folder alone can show),
   runs ``describe`` and judges it, times it, measures the longest path the
   folder would make once installed, looks at the names a zip of it would
   hold, and runs ``describe`` again from a copy under a folder with a
   non-ASCII name (TEMP / TMP / APPDATA too). It executes your entry (twice,
   with ``describe``): check only folders you trust. Without ``--dev`` the
   folder is checked as an installed plugin (the folder's name is the id,
   the entry is an .exe in it, a long path or a name a zip refuses fails
   the check); ``--dev`` checks it as a dev folder (the entry may be
   ``python worker.py``; paths and names are only noted). A ``taguyan.``
   id is noted (Autona reserves it unless the plugin comes from its own
   repository); ``--source manual`` checks it as a hand-installed zip,
   where it is refused. Exit code: 0 OK, 1 not OK.

   ``run`` treats the folder as a dev folder: it writes ``request.json``
   into a new job folder (``--work``, else ``<TEMP>/autona-plugin/<id>/``),
   starts the worker as Autona does (the environment's subtraction, stdin
   kept open, a job object on Windows: BELOW_NORMAL, the light lane's memory
   limit, everything it started ends with it), reads its events with
   Autona's rules and judges the end with Autona's table (appendix A.6).
   ``--cancel-after`` sends the cancel after N seconds; ``--timeout``
   replaces the job's time limit. The values are checked as Autona checks
   them (a file / folder setting is given as an absolute path). Exit code:
   0 done, 1 any other end (a worker that could not be launched included:
   failed / launch_failed), 2 refused or the job folder could not be made
   (the reason is printed). ``--secret`` puts the value on the command line
   (your shell's history keeps it): use a test value, not your real one.

   Autona's own test suite runs the same fake worker through this fake host
   and through Autona's engine and holds the two to the same results.

   The fake host's API (outside ``__all__``): ``check_folder``
   ``check_manifest`` ``describe_facts`` ``judge_describe`` ``prepare_run``
   ``execute`` ``fake_run`` ``cli``, and the rules they are made of:
   ``parse_event`` ``build_environment`` ``end_state`` ``entry_argv``
   ``check_artifact`` ``check_field_value`` ``check_secret_value``
   ``zip_name_problem``.

What this fake host does not do: Autona's lanes (busy), the blocklist,
consent, installing, hooks' pending / rate limits, the live state (pass a
``state`` input yourself: ``--input now=state.json``; Autona's samples are
in docs/plugins/inputs/), and a hook's job folder removal.
"""

import argparse
import hashlib
import json
import math
import ntpath
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unicodedata

SDK_VERSION = "1.0.0"

# The worker's API (the fake host's names are outside it).
__all__ = [
    "SDK_VERSION", "PROTOCOL", "Cancelled", "PluginError", "Done", "Request",
    "setup_stdio", "emit", "stage", "progress", "log", "warning", "artifact", "emit_done",
    "emit_error", "emit_cancelled", "watch_cancel", "cancel_requested", "check_cancel",
    "wait_cancel", "plugin_dir", "read_manifest", "describe_answer", "describe",
    "read_request", "pick_text", "run_worker", "main",
]

# ── the contract (docs/plugins/contract_1.json; Autona's tests pin these) ──

PROTOCOL = 1
MANIFEST = 1
HOST_SPEAKS_PROTOCOLS = (1,)
HOST_READS_MANIFESTS = (1,)
HOST_PLATFORM = "windows-x64"

PLUGIN_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,18}\.[a-z0-9][a-z0-9-]{0,19}$"
PLUGIN_ID_MAX_LENGTH = 40
RESERVED_ID_PREFIX = "taguyan."
JOB_ID_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"
FIELD_KEY_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"
HOOK_KEY_PATTERN = r"^[a-z][a-z0-9_]{0,31}/[a-z][a-z0-9_]{0,31}$"
NAME_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"
PLATFORM_PATTERN = r"^[a-z0-9][a-z0-9-]{0,31}$"
VERSION_PATTERN = r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
RUN_ID_PATTERN = r"^[0-9]{8}-[0-9]{6}-[a-z0-9]{4}$"
ERROR_CODE_PATTERN = r"^[a-z0-9_]{1,64}$"
FILE_EXTENSION_PATTERN = r"^[a-z0-9]{1,10}$"
FILE_EXTENSIONS_MAX = 20
GITHUB_REPO_PATTERN = r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$"
LOCALE_PATTERN = r"^[a-z]{2}(-[A-Z]{2})?$"
RESERVED_ENTRY_PREFIX = "@"
FALLBACK_ERROR_CODE = "error"

EVENT_TYPES = ("stage", "progress", "log", "warning", "done", "error", "cancelled")
TERMINAL_EVENTS = ("done", "error", "cancelled")
ARTIFACT_KINDS = ("text", "image", "file", "json")
ARTIFACT_REJECTIONS = ("outside_out_dir", "symlink_or_junction", "hard_link", "missing",
                       "not_a_file", "too_large", "bad_extension", "unknown_kind")
TEXT_ARTIFACT_MAX_BYTES = 64 * 1024
IMAGE_ARTIFACT_MAX_BYTES = 20 * 1024 * 1024
IMAGE_ARTIFACT_EXTENSIONS = ("png", "jpg", "jpeg", "webp")

SUMMARY_MAX_LENGTH = 500
MAX_WARNINGS = 200
MAX_ARTIFACTS = 50
LINE_MAX_BYTES = 1024 * 1024
WORKER_LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_MESSAGE_MAX_LENGTH = 2000
ERROR_MESSAGE_MAX_LENGTH = 2000
ERROR_HINT_MAX_LENGTH = 1000
LABEL_MAX_LENGTH = 200
STAGE_MAX_LENGTH = 64
LIGHT_TIMEOUT_SECONDS = (1, 300)
HEAVY_TIMEOUT_SECONDS = (1, 43200)
HOOK_TIMEOUT_CAP_SECONDS = 30
LIGHT_MEMORY_MB = (64, 1024)
HOOK_JOB_MEMORY_MB_MAX = 512
DESCRIBE_TIMEOUT_SECONDS = 60
CANCEL_GRACE_SECONDS = 10
MANIFEST_MAX_BYTES = 256 * 1024
ICON_MAX_BYTES = 256 * 1024
NAME_MAX_LENGTH = 80
DESCRIPTION_MAX_LENGTH = 500
URL_MAX_LENGTH = 2048
SELECT_MAX_OPTIONS = 100
TEXT_MAX_LENGTH = {"text_default": 200, "textarea_default": 4000, "cap": 20000}
SECRET_MAX_LENGTH = 4096
INSTALLED_PATH_MAX_LENGTH = 259

VALUE_FIELD_TYPES = ("text", "textarea", "number", "bool", "select", "file", "folder",
                     "secret")
SOURCE_FIELD_TYPES = ("match_log", "match_record", "state", "result_image",
                      "thumbnail_image", "archive_export")
CONTEXT_ONLY_TYPES = ("match_record", "archive_export")
INPUT_FORMATS = {"match_log": 1, "match_record": 1, "state": 1, "result_image": 1,
                 "thumbnail_image": 1, "archive_export": 1}
TRIGGER_KINDS = ("manual", "action", "hook")
ACTION_FILLS = {"result_image": "result_image", "thumbnail_image": "thumbnail_image",
                "archive_export": "archive_export"}
HOOK_FILLS = {"match_decided": "match_record", "result_image_saved": "result_image"}
LANES = ("light", "heavy")

ENV_REMOVE_PREFIXES = ("AUTONA_",)
ENV_REMOVE_NAMES = ("QT_API",)
ENV_PYTHONIOENCODING = "PYTHONIOENCODING"
ENV_PYINSTALLER_RESET = "PYINSTALLER_RESET_ENVIRONMENT"
ENV_PLUGIN_DIR = "AUTONA_PLUGIN_DIR"
ENV_PROTOCOL = "AUTONA_PROTOCOL"
ENV_SECRET_PREFIX = "AUTONA_SECRET_"
ENV_API_URL = "AUTONA_API_URL"

JOB_STATES = ("done", "error", "failed", "cancelled")
END_REASONS = ("launch_failed", "entry_missing", "timed_out", "memory_limit",
               "abnormal_exit", "autona_exited")
DESCRIBE_FAILURES = ("no_line", "not_json", "type_not_describe", "protocol_missing",
                     "no_common_protocol", "id_mismatch", "version_mismatch",
                     "nonzero_exit", "timed_out", "launch_failed")
# The manifest codes: (code, scope); settings_scope is "plugin" for every
# code a settings field can raise. id_taken (two folders of one id) cannot
# be seen from one folder.
MANIFEST_CHECKS = (
    ("manifest_unreadable", "plugin"), ("manifest_version_unknown", "plugin"),
    ("invalid_field", "plugin"), ("invalid_job_field", "job"), ("unknown_key", "warning"),
    ("id_folder_mismatch", "plugin"), ("id_reserved", "plugin"), ("id_taken", "plugin"),
    ("protocol_unsupported", "plugin"), ("platform_unsupported", "plugin"),
    ("min_autona_newer", "plugin"), ("entry_reserved", "plugin"),
    ("entry_outside", "plugin"), ("entry_missing", "plugin"), ("icon_invalid", "warning"),
    ("key_duplicate", "job"), ("secret_outside_settings", "job"),
    ("source_in_settings", "plugin"), ("field_type_unknown", "job"),
    ("hook_event_unknown", "job"), ("action_target_unknown", "job"),
    ("hook_duplicate", "job"), ("trigger_needs_light_lane", "job"), ("fill_count", "job"),
    ("trigger_unfillable", "job"), ("manual_with_context", "job"),
    ("hook_param_needs_default", "job"), ("default_invalid", "job"),
    ("timeout_out_of_range", "job"), ("memory_out_of_range", "job"),
    ("job_unreachable", "warning"),
)
MANIFEST_FILE = "plugin.json"
REQUEST_FILE = "request.json"
LOG_FILE = "worker.log"
INPUTS_DIR = "inputs"
OUT_DIR = "out"

# The fake host's own limits (not the contract's).
DESCRIBE_SLOW_SECONDS = 10.0
# Room the Autona folder's own path needs (C:\Users\<name>\...\Autona).
# Only the plugin folder under <Autona>\plugins is measured: a path the
# plugin makes in its user_data folder (data_dir) is not counted.
PATH_ROOM_WARN = 80
PATH_ROOM_MIN = 40
NON_ASCII_FOLDER = "Autona テスト ü"
# Folders the path and name checks skip (a dev tree's tools and caches).
SKIPPED_FOLDERS = ("__pycache__",)
# How many queued stdout lines one look takes before the time limit and
# the cancel are looked at again, and how many may wait (the reader then
# waits: the worker's writes block, as they do on a full pipe).
_TAKE_PER_LOOK = 256
_QUEUED_LINES_MAX = 1024
# RunResult.events keeps at most this many (a run of progress events is
# folded into its latest).
RESULT_EVENTS_MAX = 10000
# A secret value shorter than this is not masked (it would mask too much).
SECRET_MASK_MIN_LENGTH = 8
SECRET_MASK = "***"


# ═══ 1. the worker's helpers ════════════════════════════════════════════

class Cancelled(Exception):
    """Raised by ``check_cancel`` once the host asked to stop; ``main``
    answers it with ``cancelled`` and exit 130."""


class PluginError(Exception):
    """A failure the user can fix: ``main`` answers it with ``error`` and
    exit 2. ``code`` matches ^[a-z0-9_]{1,64}$ (your own names); ``message``
    and ``hint`` are texts (a string or {"ja": ..., "en": ...}) and must
    hold no secret."""

    def __init__(self, code, message, hint=None):
        super().__init__(code)
        self.code = code
        self.message = message
        self.hint = hint


class Done:
    """What a job handler returns: ``done`` with this summary and these
    artifacts (``artifact(...)`` items; paths relative to out_dir)."""

    def __init__(self, summary=None, artifacts=()):
        self.summary = summary
        self.artifacts = list(artifacts)


_out_lock = threading.Lock()
# Per run (run_worker resets them): the terminal event written, the cancel.
_terminal = {"type": None}
_cancel = threading.Event()
_watcher = {"thread": None}


def setup_stdio():
    """stdout as UTF-8 with a flush per line (the host reads UTF-8 and drops
    what it cannot decode), stderr as UTF-8 too. ``main`` calls it."""
    for stream, errors in ((sys.stdout, "strict"), (sys.stderr, "backslashreplace")):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors=errors, line_buffering=True)
            except (ValueError, OSError):
                pass


def _secret_values():
    """The AUTONA_SECRET_* values long enough to mask, longest first."""
    values = {value for name, value in os.environ.items()
              if name.upper().startswith(ENV_SECRET_PREFIX)
              and len(value) >= SECRET_MASK_MIN_LENGTH}
    return sorted(values, key=len, reverse=True)


def _mask(text, secrets=None):
    """``text`` with every secret value written as ***."""
    for value in _secret_values() if secrets is None else secrets:
        text = text.replace(value, SECRET_MASK)
    return text


def _finite(value):
    """A number JSON can hold: NaN and the infinities are None."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _clean(value, secrets):
    """An event's value with its strings masked and its non-finite numbers
    None."""
    if isinstance(value, str):
        return _mask(value, secrets) if secrets else value
    if isinstance(value, dict):
        return {(_clean(key, secrets) if isinstance(key, str) else key): _clean(item, secrets)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item, secrets) for item in value]
    return _finite(value)


def emit(event):
    """Write one event (a dict with ``type``) as one JSON line and flush. A
    secret's value is written as ***, NaN / infinity as null."""
    event = _clean(event, _secret_values())
    try:
        line = json.dumps(event, ensure_ascii=False, allow_nan=False)
        line.encode("utf-8")
    except UnicodeEncodeError:  # a lone surrogate: escape it
        line = json.dumps(event, ensure_ascii=True, allow_nan=False)
    with _out_lock:
        if event.get("type") in TERMINAL_EVENTS:
            _terminal["type"] = event["type"]
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def _drop_none(event):
    return {key: value for key, value in event.items() if value is not None}


def stage(stage, label=None, index=None, count=None):
    """A new stage: ``stage`` an identifier, ``label`` a text, ``index`` of
    ``count`` (from 1)."""
    emit(_drop_none({"type": "stage", "stage": stage, "label": label, "index": index,
                     "count": count}))


def progress(fraction=None, overall=None, eta=None, stage=None):
    """Progress: ``fraction`` of the stage and ``overall`` (0 to 1), ``eta``
    in seconds (NaN / infinity left out)."""
    emit(_drop_none({"type": "progress", "stage": stage, "fraction": _finite(fraction),
                     "overall": _finite(overall), "eta": _finite(eta)}))


def log(message):
    """A line the host shows (the latest one only)."""
    emit({"type": "log", "message": message})


def warning(message):
    """A warning kept on the run (the first 200)."""
    emit({"type": "warning", "message": message})


def artifact(path, kind, label=None):
    """One ``done`` artifact: ``path`` relative to out_dir, ``kind`` text /
    image / file / json."""
    return _drop_none({"path": path, "kind": kind, "label": label})


def emit_done(summary=None, artifacts=None):
    """The terminal event: success (then exit 0)."""
    emit(_drop_none({"type": "done", "summary": summary,
                     "artifacts": list(artifacts) if artifacts else None}))


def emit_error(code, message, hint=None):
    """The terminal event: a failure the user can fix (then exit 2)."""
    emit(_drop_none({"type": "error", "code": code, "message": message, "hint": hint}))


def emit_cancelled():
    """The terminal event: stopped on the host's request (then exit 130)."""
    emit({"type": "cancelled"})


def watch_cancel():
    """Watch stdin on a daemon thread: a ``cancel`` line or stdin closed
    (Autona gone) sets the cancel; other lines are ignored. ``main`` starts
    it for a run (a watcher still reading stdin is kept)."""
    thread = _watcher["thread"]
    if thread is not None and thread.is_alive():
        return

    def watch():
        try:
            for line in sys.stdin:
                if line.strip() == "cancel":
                    break
        except (OSError, ValueError, AttributeError, TypeError):
            pass
        _cancel.set()

    thread = threading.Thread(target=watch, name="autona-plugin-cancel", daemon=True)
    _watcher["thread"] = thread
    thread.start()


def cancel_requested():
    """Whether the host asked to stop: a ``cancel`` line, or stdin closed
    (Autona closes it with the cancel, and it closes when Autona is gone)."""
    return _cancel.is_set()


def check_cancel():
    """Raise Cancelled once cancel_requested(). Call it between steps (a
    send that cannot be taken back may finish first and end ``done``)."""
    if _cancel.is_set():
        raise Cancelled()


def wait_cancel(seconds):
    """Sleep up to ``seconds``, waking at the cancel; True when cancelled."""
    return _cancel.wait(seconds)


def plugin_dir():
    """The plugin's folder: AUTONA_PLUGIN_DIR, else the nearest folder above
    the running script (or exe) holding plugin.json, else None."""
    folder = os.environ.get(ENV_PLUGIN_DIR)
    if folder:
        return folder
    start = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False)
                                            else sys.argv[0] or "."))
    current = start
    for _ in range(4):
        if os.path.isfile(os.path.join(current, MANIFEST_FILE)):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return None


def read_manifest(folder=None):
    """plugin.json (UTF-8, a BOM accepted) of ``folder`` (plugin_dir())."""
    folder = folder or plugin_dir()
    if not folder:
        raise FileNotFoundError("no plugin folder (AUTONA_PLUGIN_DIR is not set)")
    with open(os.path.join(folder, MANIFEST_FILE), "rb") as file:
        return json.loads(file.read(MANIFEST_MAX_BYTES + 1).decode("utf-8-sig"))


def describe_answer(manifest=None):
    """describe's answer, built from plugin.json."""
    manifest = manifest if manifest is not None else read_manifest()
    return {"type": "describe", "protocol": list(HOST_SPEAKS_PROTOCOLS),
            "id": manifest.get("id"), "version": manifest.get("version")}


def describe():
    """Print describe's one line (exit 0 after it)."""
    emit(describe_answer())


class Request:
    """request.json (ADR-009 A.3), read only. The fields are attributes
    (``plugin`` ``job`` ``run_id`` ``trigger`` ``locale`` ``params``
    ``settings`` ``inputs`` ``out_dir`` ``data_dir`` ``host``); ``raw`` is
    the whole object. The cancel is the module's (cancel_requested,
    check_cancel, wait_cancel)."""

    def __init__(self, raw, path=None):
        self.raw = raw
        self.path = path
        self.protocol = raw.get("protocol")
        self.plugin = raw.get("plugin")
        self.job = raw.get("job")
        self.run_id = raw.get("run_id")
        self.trigger = raw.get("trigger") or {}
        self.locale = raw.get("locale") or "en"
        self.params = raw.get("params") or {}
        self.settings = raw.get("settings") or {}
        self.inputs = raw.get("inputs") or {}
        self.out_dir = raw.get("out_dir")
        self.data_dir = raw.get("data_dir")
        self.host = raw.get("host") or {}

    def input_path(self, key):
        """The absolute path of input ``key``, or None when it was not
        filled."""
        item = self.inputs.get(key)
        return item.get("path") if isinstance(item, dict) else None

    def secret(self, key):
        """A secret setting's value (environment AUTONA_SECRET_<KEY>), or
        None when it is not set. Secrets are never in request.json."""
        return os.environ.get(ENV_SECRET_PREFIX + key.upper())

    def out_path(self, name):
        """A path in out_dir (the folder exists)."""
        return os.path.join(self.out_dir, name)

    def text(self, value):
        """A text (a string or a locale map) in this run's locale (then en,
        ja, the first value)."""
        return pick_text(value, self.locale)


def read_request(path):
    """The Request of ``path`` (UTF-8)."""
    with open(path, encoding="utf-8") as file:
        return Request(json.load(file), path)


def pick_text(value, locale="en"):
    """A text in ``locale`` (then en, ja, the first value), as the host
    shows it; None when it is not a text."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and value:
        for key in (locale, "en", "ja"):
            if isinstance(value.get(key), str):
                return value[key]
        first = next(iter(value.values()))
        return first if isinstance(first, str) else None
    return None


def run_worker(jobs, argv=None):
    """``describe`` or ``run <request.json>`` -> the exit code. ``jobs`` is
    {job id: handler(request)} or one handler for every job. A handler
    returns None (``done``), a Done, or a text (the summary); raises
    PluginError / Cancelled; or writes its own terminal event. Each call
    starts with no terminal event and no cancel."""
    setup_stdio()
    _terminal["type"] = None
    _cancel.clear()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["describe"]:
        try:
            describe()
        except (OSError, ValueError) as problem:
            print(f"describe: plugin.json could not be read: {problem}", file=sys.stderr)
            return 1
        return 0
    if len(argv) != 2 or argv[0] != "run":
        print("usage: <worker> describe | run <request.json>", file=sys.stderr)
        return 2
    request = read_request(argv[1])
    handler = jobs if callable(jobs) else jobs.get(request.job)
    if handler is None:
        emit_error("unknown_job", f"unknown job: {request.job}")
        return 2
    watch_cancel()
    try:
        result = handler(request)
    except Cancelled:
        if _terminal["type"] is None:
            emit_cancelled()
        return _exit_code()
    except PluginError as failure:
        if _terminal["type"] is None:
            emit_error(failure.code, failure.message, failure.hint)
        return _exit_code()
    except Exception:
        sys.stderr.write(_mask(traceback.format_exc()))
        sys.stderr.flush()
        return 1
    if _terminal["type"] is None:
        if isinstance(result, Done):
            emit_done(result.summary, result.artifacts)
        elif result is None:
            emit_done()
        else:
            emit_done(result)
    return _exit_code()


def _exit_code():
    return {"done": 0, "error": 2, "cancelled": 130}.get(_terminal["type"], 1)


def main(jobs, argv=None):
    """run_worker, then exit with its code."""
    code = run_worker(jobs, argv)
    try:
        sys.stdout.flush()
    except (OSError, ValueError):
        pass
    sys.exit(code)


# ═══ 2. the fake host ═══════════════════════════════════════════════════

# ── the events (Autona's PluginRunner.ParseEvent) ──

def _string(value, limit):
    return value[:limit] if isinstance(value, str) else None


def _text(value, limit):
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, dict):
        text = {key: item[:limit] for key, item in value.items()
                if isinstance(key, str) and isinstance(item, str)}
        return text or None
    return None


def _number(value, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    if (low is not None and value < low) or (high is not None and value > high):
        return None
    return value


def _count(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _artifacts(items):
    if not isinstance(items, list):
        return []
    taken = []
    for item in items[:MAX_ARTIFACTS]:
        if not isinstance(item, dict):
            continue
        path, kind = item.get("path"), item.get("kind")
        if not isinstance(path, str) or not path or kind not in ARTIFACT_KINDS:
            continue
        taken.append({"path": path, "kind": kind,
                      "label": _text(item.get("label"), LABEL_MAX_LENGTH)})
    return taken


def parse_event(obj):
    """The event the host takes from one stdout object, as a dict with
    ``type``, the contract's fields (those that do not fit dropped as None,
    texts truncated, a bad ``error.code`` made ``error``) and ``raw`` (the
    whole object); None when it is not an event (the line goes to
    worker.log)."""
    if not isinstance(obj, dict) or not isinstance(obj.get("type"), str):
        return None
    kind = obj["type"]
    if kind not in EVENT_TYPES:
        return None
    if kind == "stage":
        event = {"stage": _string(obj.get("stage"), STAGE_MAX_LENGTH) or "",
                 "label": _text(obj.get("label"), LABEL_MAX_LENGTH),
                 "index": _count(obj.get("index")), "count": _count(obj.get("count"))}
    elif kind == "progress":
        event = {"stage": _string(obj.get("stage"), STAGE_MAX_LENGTH),
                 "fraction": _number(obj.get("fraction"), 0, 1),
                 "overall": _number(obj.get("overall"), 0, 1),
                 "eta": _number(obj.get("eta"), 0)}
    elif kind in ("log", "warning"):
        event = {"message": _text(obj.get("message"), LOG_MESSAGE_MAX_LENGTH) or ""}
    elif kind == "done":
        event = {"summary": _text(obj.get("summary"), SUMMARY_MAX_LENGTH),
                 "artifacts": _artifacts(obj.get("artifacts"))}
    elif kind == "error":
        code = obj.get("code")
        if not isinstance(code, str) or not re.fullmatch(ERROR_CODE_PATTERN, code):
            code = FALLBACK_ERROR_CODE
        event = {"code": code,
                 "message": _text(obj.get("message"), ERROR_MESSAGE_MAX_LENGTH) or "",
                 "hint": _text(obj.get("hint"), ERROR_HINT_MAX_LENGTH)}
    else:
        event = {}
    event["type"] = kind
    event["raw"] = obj
    return event


def _loads(text):
    if not text.startswith("{"):
        return None
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return None


class _Lines:
    """A byte stream split on LF (a CR before it dropped), holding at most
    LINE_MAX_BYTES + 1 bytes of a line: each line is (bytes, size)."""

    def __init__(self, limit=LINE_MAX_BYTES):
        self._limit = limit
        self._buf = bytearray()
        self._dropped = 0

    def feed(self, data):
        lines = []
        start = 0
        while True:
            end = data.find(b"\n", start)
            if end < 0:
                self._keep(data[start:])
                return lines
            self._keep(data[start:end])
            lines.append(self._take())
            start = end + 1

    def flush(self):
        if not self._buf and not self._dropped:
            return None
        return self._take()

    def _keep(self, part):
        room = max(self._limit + 1 - len(self._buf), 0)
        self._buf += part[:room]
        self._dropped += max(len(part) - room, 0)

    def _take(self):
        line, dropped = bytes(self._buf), self._dropped
        self._buf.clear()
        self._dropped = 0
        if line.endswith(b"\r"):
            line = line[:-1]
        return line, len(line) + dropped


def _decode(data):
    return data.decode("utf-8", "replace")


def _long_line(where, line, size):
    return (f"{where}{_decode(line[:1024])}... ({size} bytes: over the {LINE_MAX_BYTES}-byte "
            f"line limit)")


# ── the environment and the end (Autona's PluginRunner) ──

def build_environment(parent, plugin_dir, protocol=None, secrets=None, api_url=None):
    """contract_1.json environment: ``parent`` minus every AUTONA_* and
    QT_API (case-insensitive), plus PYTHONIOENCODING,
    PYINSTALLER_RESET_ENVIRONMENT, AUTONA_PLUGIN_DIR and, for a run
    (``protocol`` set), AUTONA_PROTOCOL, the secrets (AUTONA_SECRET_<KEY>
    names) and AUTONA_API_URL."""
    added = {ENV_PYTHONIOENCODING: "utf-8", ENV_PYINSTALLER_RESET: "1",
             ENV_PLUGIN_DIR: plugin_dir}
    if protocol is not None:
        added[ENV_PROTOCOL] = str(protocol)
        seen = set()
        for name, value in (secrets or {}).items():
            if (not isinstance(name, str) or "=" in name or "\0" in name
                    or not name.upper().startswith(ENV_SECRET_PREFIX)):
                raise ValueError(f"a secret's environment name must start with "
                                 f"{ENV_SECRET_PREFIX} (no '=', NUL)")
            if name.upper() in seen:
                raise ValueError("two secrets' names differ only in case")
            seen.add(name.upper())
            if not isinstance(value, str) or "\0" in value:
                raise ValueError("a secret must be a string without NUL")
            added[name] = value
        if api_url:
            added[ENV_API_URL] = api_url
    removed = {name.upper() for name in ENV_REMOVE_NAMES}
    added_names = {name.upper() for name in added}
    env = {}
    for name, value in parent.items():
        upper = name.upper()
        if upper in removed or upper in added_names or upper.startswith(ENV_REMOVE_PREFIXES):
            continue
        env[name] = value
    env.update(added)
    return env


def end_state(final, exit_code, cancel_requested=False, timed_out=False,
              launch_failed=False, entry_missing=False, memory_limit_hit=False):
    """contract_1.json end -> (state, reason or None): a launch failure,
    then timed_out, then the table (appendix A.6)."""
    if launch_failed:
        return "failed", ("entry_missing" if entry_missing else "launch_failed")
    if timed_out:
        return "failed", "timed_out"
    if exit_code == 0 and final == "done":
        return "done", None
    if exit_code == 130 or final == "cancelled":
        return "cancelled", None
    if exit_code == 2 and final == "error":
        return "error", None
    if cancel_requested and final is None:
        return "cancelled", None
    return "failed", ("memory_limit" if memory_limit_hit else "abnormal_exit")


def entry_argv(folder, entry, dev):
    """The manifest's entry as an argv: an installed plugin's first word is
    a path in its folder; a dev plugin's is too when it names a file there
    or a path, else a command on PATH as given. ValueError when empty."""
    if not entry or not isinstance(entry[0], str) or not entry[0]:
        raise ValueError("an empty entry")
    first, rest = entry[0], list(entry[1:])
    if os.path.isabs(first):
        return [first, *rest]
    inside = os.path.normpath(os.path.join(folder, first))
    if not dev or os.path.isfile(inside) or "/" in first or os.sep in first:
        return [inside, *rest]
    return [first, *rest]


def _run_argv(folder, entry, dev):
    """A run's argv (Autona's PluginJobs._Argv): the first word resolved in
    the folder (a dev one may name a command on PATH), the later words of a
    dev entry resolved when they name a file in the folder. None when an
    installed entry leaves the folder."""
    folder = os.path.abspath(folder)
    resolved = []
    for index, word in enumerate(entry):
        candidate = os.path.normpath(os.path.join(folder, word))
        in_folder = not os.path.isabs(word) and _below(folder, candidate)
        if index == 0 and not dev:
            if not in_folder:
                return None
            resolved.append(candidate)
        elif dev and in_folder and os.path.isfile(candidate):
            resolved.append(candidate)
        else:
            resolved.append(word)
    return resolved


def _below(base, path):
    base, path = os.path.normcase(base), os.path.normcase(path)
    try:
        return os.path.commonpath([base, path]) == base and path != base
    except ValueError:
        return False


def _resolve_command(argv, env):
    """A bare first word looked up on the child's PATH (Windows searches
    the parent's, so the fake host resolves it itself)."""
    first = argv[0]
    if os.path.isabs(first) or "/" in first or os.sep in first:
        return argv
    found = shutil.which(first, path=env.get("PATH") or env.get("Path"))
    return [found, *argv[1:]] if found else argv


# ── Windows: the job object ──

_WINDOWS = sys.platform == "win32"
_CREATE_SUSPENDED = 0x00000004
_CREATE_NO_WINDOW = 0x08000000
_BELOW_NORMAL = 0x00004000
_NORMAL = 0x00000020
_MEMORY_LIMIT_REACHED = 0.95
_api = {}


def _win_api():
    if _api:
        return _api
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                            wintypes.DWORD)
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                              wintypes.DWORD, wintypes.LPDWORD)
    k32.QueryInformationJobObject.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    k32.TerminateJobObject.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    k32.CloseHandle.restype = wintypes.BOOL
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
    ntdll.NtResumeProcess.restype = ctypes.c_long

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class Io(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", Io),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    _api.update(ctypes=ctypes, k32=k32, ntdll=ntdll, Extended=Extended)
    return _api


class _Job:
    """A job object: KILL_ON_JOB_CLOSE, a priority class limit and, on the
    light lane, a job memory limit. OSError when it cannot be made."""

    def __init__(self, priority, memory_bytes=None):
        api = _win_api()
        self._api = api
        self._lock = threading.Lock()
        self._handle = api["k32"].CreateJobObjectW(None, None)
        if not self._handle:
            raise OSError(f"CreateJobObject failed ({api['ctypes'].get_last_error()})")
        self._limits = api["Extended"]()
        flags = 0x2000 | 0x20  # KILL_ON_JOB_CLOSE | PRIORITY_CLASS
        if memory_bytes:
            flags |= 0x200  # JOB_MEMORY
            self._limits.JobMemoryLimit = memory_bytes
        self._limits.BasicLimitInformation.LimitFlags = flags
        self._limits.BasicLimitInformation.PriorityClass = priority
        if not self._set():
            error = OSError(f"SetInformationJobObject failed "
                            f"({api['ctypes'].get_last_error()})")
            self.close()
            raise error

    def _set(self):
        ctypes = self._api["ctypes"]
        return self._api["k32"].SetInformationJobObject(
            self._handle, 9, ctypes.byref(self._limits), ctypes.sizeof(self._limits))

    def assign(self, proc):
        if not self._api["k32"].AssignProcessToJobObject(self._handle, int(proc._handle)):
            raise OSError(f"AssignProcessToJobObject failed "
                          f"({self._api['ctypes'].get_last_error()})")
        status = self._api["ntdll"].NtResumeProcess(int(proc._handle))
        if status != 0:
            raise OSError(f"NtResumeProcess failed (0x{status & 0xffffffff:08x})")

    def set_priority(self, priority):
        with self._lock:
            if self._handle:
                self._limits.BasicLimitInformation.PriorityClass = priority
                self._set()

    def peak_memory(self):
        ctypes = self._api["ctypes"]
        with self._lock:
            if not self._handle:
                return None
            info = self._api["Extended"]()
            if not self._api["k32"].QueryInformationJobObject(
                    self._handle, 9, ctypes.byref(info), ctypes.sizeof(info), None):
                return None
            return int(info.PeakJobMemoryUsed)

    def terminate(self):
        with self._lock:
            if self._handle:
                self._api["k32"].TerminateJobObject(self._handle, 1)

    def close(self):
        with self._lock:
            handle, self._handle = self._handle, None
        if handle:
            self._api["k32"].CloseHandle(handle)


class _LaunchError(Exception):
    def __init__(self, message, entry_missing=False):
        super().__init__(message)
        self.entry_missing = entry_missing


def _spawn(argv, cwd, env, priority, memory_bytes, stdin, stderr):
    """(Popen, job or None) of ``argv`` started (in its job on Windows) and
    running; _LaunchError with nothing left running."""
    job = None
    if _WINDOWS:
        try:
            job = _Job(priority, memory_bytes)
        except OSError as problem:
            raise _LaunchError(str(problem)) from None
        flags = {"creationflags": _CREATE_SUSPENDED | _CREATE_NO_WINDOW}
    else:
        flags = {"start_new_session": True}
    try:
        proc = subprocess.Popen(list(argv), cwd=cwd, env=dict(env), stdin=stdin,
                                stdout=subprocess.PIPE, stderr=stderr, bufsize=0, **flags)
    except Exception as problem:
        if job is not None:
            job.close()
        text = (problem.strerror if isinstance(problem, OSError) and problem.strerror
                else str(problem))
        raise _LaunchError(f"{type(problem).__name__}: {text}",
                           entry_missing=isinstance(problem, FileNotFoundError)
                           and os.path.isdir(cwd)) from None
    if job is not None:
        try:
            job.assign(proc)
        except OSError as problem:
            try:
                proc.kill()
                proc.wait(2)
            except (OSError, subprocess.TimeoutExpired):
                pass
            job.close()
            raise _LaunchError(str(problem)) from None
    return proc, job


def _kill(proc, job):
    try:
        if job is not None:
            job.terminate()
        elif not _WINDOWS:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        proc.kill()
    except OSError:
        pass


class _WorkerLog:
    """worker.log, appended to, WORKER_LOG_MAX_BYTES in all; written from
    two threads."""

    def __init__(self, path):
        self._lock = threading.Lock()
        self._full = False
        self.note = (f"--- worker.log reached {WORKER_LOG_MAX_BYTES} bytes: the rest is "
                     f"dropped ---\n")
        try:
            self._file = open(path, "ab")
            self._written = self._file.tell()
        except OSError:
            self._file, self._written = None, 0

    def write(self, text):
        data = (text.rstrip("\r\n") + "\n").encode("utf-8", "replace")
        with self._lock:
            if self._file is None or self._full:
                return
            if len(data) > WORKER_LOG_MAX_BYTES - len(self.note) - self._written:
                self._full = True
                data = self.note.encode("utf-8")
            try:
                self._file.write(data)
                self._file.flush()
                self._written += len(data)
            except (OSError, ValueError):
                pass

    def close(self):
        with self._lock:
            file, self._file = self._file, None
        if file is not None:
            file.close()


# ── a run ──

class Prepared:
    """A run ready to start: ``argv`` (entry + ``run <request.json>``),
    ``cwd`` (the job folder), ``env``, ``lane``, ``timeout`` (seconds or
    None), ``memory_mb``, ``log_path``, ``request`` (what request.json
    holds) and ``folder`` (the job folder)."""

    def __init__(self, argv, cwd, env, lane, timeout, memory_mb, log_path,
                 request=None, folder=None):
        self.argv = list(argv)
        self.cwd = cwd
        self.env = dict(env)
        self.lane = lane
        self.timeout = timeout
        self.memory_mb = memory_mb
        self.log_path = log_path
        self.request = request
        self.folder = folder or cwd


class RunResult:
    """How a run ended: ``state`` (done / error / failed / cancelled),
    ``reason`` (a failed run's), ``exit_code``, ``events`` (parse_event's
    dicts, in order: a run of progress events kept as its latest, at most
    RESULT_EVENTS_MAX in all, ``events_dropped`` counting what that left
    out -- on_event sees every event), ``final`` (the last terminal event),
    ``warnings`` (the warnings' messages, the first MAX_WARNINGS),
    ``cancel_requested``, ``timed_out``, ``finished_before_cancel``,
    ``peak_memory_bytes``, ``rejected`` ([(artifact path, rejection)] of
    the final artifacts, Autona's checks) and ``folder`` (the job
    folder)."""

    def __init__(self, **values):
        self.state = None
        self.reason = None
        self.exit_code = None
        self.events = []
        self.events_dropped = 0
        self.final = None
        self.warnings = []
        self.cancel_requested = False
        self.timed_out = False
        self.finished_before_cancel = False
        self.peak_memory_bytes = None
        self.rejected = []
        self.folder = None
        self.__dict__.update(values)

    def keep(self, event):
        """Add a taken event to ``events`` (folded and capped)."""
        events = self.events
        if event["type"] == "progress" and events and events[-1]["type"] == "progress":
            events[-1] = event
        elif len(events) < RESULT_EVENTS_MAX:
            events.append(event)
        else:
            self.events_dropped += 1

    def as_dict(self):
        def plain(event):
            return {key: value for key, value in event.items() if key != "raw"}
        return {"state": self.state, "reason": self.reason, "exit_code": self.exit_code,
                "final": plain(self.final) if self.final else None,
                "warnings": self.warnings, "cancel_requested": self.cancel_requested,
                "timed_out": self.timed_out,
                "finished_before_cancel": self.finished_before_cancel,
                "peak_memory_bytes": self.peak_memory_bytes,
                "rejected": [list(item) for item in self.rejected], "folder": self.folder,
                "events_dropped": self.events_dropped}


def execute(prepared, cancel_after=None, grace=CANCEL_GRACE_SECONDS, on_event=None,
            poll=0.05, clock=time.monotonic):
    """Start ``prepared`` and judge its end as Autona does (ADR-009 A.4 to
    A.6): stdin kept open; at ``cancel_after`` seconds (or past the time
    limit, then marked timed_out -- unless the cancel came first) the
    priority is raised to NORMAL, ``cancel`` written and stdin closed, and
    ``grace`` seconds later the job is ended. ``on_event(event)`` sees each
    taken event; what it raises is written to worker.log (the first time)
    and the run goes on. A slow on_event does not hold up the time limit or
    the cancel: the queued lines are taken a few at a time between looks at
    the clock, and at most _QUEUED_LINES_MAX wait (the worker's writes then
    block, as on a full pipe). Whatever leaves this early (an interrupt)
    ends the worker and its job first."""
    result = RunResult(folder=prepared.folder)
    log = _WorkerLog(prepared.log_path)
    memory = (prepared.memory_mb * 1024 * 1024
              if prepared.lane == "light" and prepared.memory_mb else None)
    try:
        proc, job = _spawn(prepared.argv, prepared.cwd, prepared.env, _BELOW_NORMAL, memory,
                           subprocess.PIPE, subprocess.PIPE)
    except _LaunchError as problem:
        log.write(f"--- could not start: {problem} ---")
        log.close()
        result.state, result.reason = end_state(None, None, launch_failed=True,
                                                entry_missing=problem.entry_missing)
        return result
    except Exception as problem:  # a malformed argv, say
        log.write(f"--- could not start: {type(problem).__name__}: {problem} ---")
        log.close()
        result.state, result.reason = end_state(None, None, launch_failed=True)
        return result

    import queue
    lines = queue.Queue(maxsize=_QUEUED_LINES_MAX)
    stop = threading.Event()

    def put(item):
        while not stop.is_set():
            try:
                lines.put(item, timeout=0.1)
                return True
            except queue.Full:
                pass
        return False

    def read_stdout():
        splitter = _Lines()
        fd = proc.stdout.fileno()
        try:
            while True:
                data = os.read(fd, 65536)
                if not data:
                    break
                for item in splitter.feed(data):
                    if not put(item):
                        return
        except (OSError, ValueError):
            pass
        tail = splitter.flush()
        if tail is None or put(tail):
            put(None)

    def read_stderr():
        splitter = _Lines()
        fd = proc.stderr.fileno()

        def write(line, size):
            log.write(_long_line("", line, size) if size > LINE_MAX_BYTES else _decode(line))
        try:
            while True:
                data = os.read(fd, 65536)
                if not data:
                    break
                for item in splitter.feed(data):
                    write(*item)
        except (OSError, ValueError):
            pass
        tail = splitter.flush()
        if tail is not None:
            write(*tail)

    out_thread = threading.Thread(target=read_stdout, name="autona-plugin-stdout", daemon=True)
    err_thread = threading.Thread(target=read_stderr, name="autona-plugin-stderr", daemon=True)
    out_thread.start()
    err_thread.start()

    state = {"warnings": 0, "final": None, "on_event_failed": False}

    def take(line, size):
        if size > LINE_MAX_BYTES:
            log.write(_long_line("[stdout] ", line, size))
            return
        text = _decode(line).strip()
        if not text:
            return
        event = parse_event(_loads(text))
        if event is None:
            log.write(f"[stdout] {text}")
            return
        if event["type"] == "warning":
            state["warnings"] += 1
            if state["warnings"] > MAX_WARNINGS:
                return
            result.warnings.append(event["message"])
        if event["type"] in TERMINAL_EVENTS:
            state["final"] = event
        result.keep(event)
        if on_event is None:
            return
        try:
            on_event(event)
        except Exception as problem:
            if not state["on_event_failed"]:
                state["on_event_failed"] = True
                log.write(f"--- the host's on_event raised {type(problem).__name__}: "
                          f"{problem} ---")

    started = clock()
    deadline = None if prepared.timeout is None else started + prepared.timeout
    cancel_at = None if cancel_after is None else started + cancel_after
    kill_at = None
    cancel_sent = killed = stdout_done = False
    exited_at = peak = None
    try:
        while True:
            try:
                item = lines.get(timeout=poll)
                look_until = clock() + poll
                taken = 0
                while True:
                    if item is None:
                        stdout_done = True
                        break
                    take(*item)
                    taken += 1
                    if taken >= _TAKE_PER_LOOK or clock() >= look_until:
                        break
                    item = lines.get_nowait()
            except queue.Empty:
                pass
            now = clock()
            if proc.poll() is None:
                if cancel_at is not None and now >= cancel_at and not result.cancel_requested:
                    result.cancel_requested = True
                if (deadline is not None and not result.timed_out
                        and not result.cancel_requested and not cancel_sent
                        and now >= deadline):
                    result.timed_out = True
                if (result.cancel_requested or result.timed_out) and not cancel_sent:
                    cancel_sent = True
                    kill_at = now + grace
                    if job is not None:
                        job.set_priority(_NORMAL)
                    try:
                        proc.stdin.write(b"cancel\n")
                        proc.stdin.flush()
                    except (OSError, ValueError):
                        pass
                    try:
                        proc.stdin.close()
                    except (OSError, ValueError):
                        pass
                if kill_at is not None and now >= kill_at and not killed:
                    killed = True
                    log.write("--- stopped by the host ---")
                    _kill(proc, job)
                continue
            if exited_at is None:
                exited_at = now
                if job is not None:
                    peak = job.peak_memory()
                    job.close()
            if stdout_done or now - exited_at >= 2.0:
                break
        # What was queued after the last look (at most the queue's size).
        for _ in range(lines.qsize()):
            try:
                item = lines.get_nowait()
            except queue.Empty:
                break
            if item is None:
                break
            take(*item)
    finally:
        stop.set()
        if proc.poll() is None:  # left early: the worker does not outlive the host
            _kill(proc, job)
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                pass
        if job is not None:
            job.close()
        out_thread.join(2.0)
        err_thread.join(2.0)
        for stream, reader in ((proc.stdin, None), (proc.stdout, out_thread),
                               (proc.stderr, err_thread)):
            try:
                if stream is not None and (reader is None or not reader.is_alive()):
                    stream.close()
            except (OSError, ValueError):
                pass
        log.write(f"--- exit {proc.returncode} ---")
        log.close()
    exit_code = proc.returncode
    final = state["final"]
    memory_hit = memory is not None and peak is not None and peak >= memory * _MEMORY_LIMIT_REACHED
    result.state, result.reason = end_state(
        final["type"] if final else None, exit_code, cancel_requested=result.cancel_requested,
        timed_out=result.timed_out, memory_limit_hit=memory_hit)
    result.exit_code = exit_code
    result.final = final
    result.peak_memory_bytes = peak
    result.finished_before_cancel = result.state == "done" and result.cancel_requested
    if final is not None and final["type"] == "done" and prepared.request:
        out_dir = prepared.request.get("out_dir")
        for item in final["artifacts"]:
            why = check_artifact(out_dir, item["path"], item["kind"])[1]
            if why is not None:
                result.rejected.append((item["path"], why))
    return result


_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT


def _is_link(path):
    """A symbolic link or a junction, as Autona's islink / isjunction (before
    Python 3.12 os.path has no isjunction: any reparse point counts)."""
    if os.path.islink(path):
        return True
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is not None:
        return isjunction(path)
    try:
        info = os.lstat(path)
    except (OSError, ValueError):
        return False
    return bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def check_artifact(out_dir, path, kind):
    """(the real path, None) of an artifact Autona accepts, else (None, the
    rejection): outside out_dir, a link or junction on the way, missing,
    not a file, a hard link, an image's extension or size, a text's size."""
    if kind not in ARTIFACT_KINDS:
        return None, "unknown_kind"
    if (not isinstance(path, str) or not path or "\x00" in path or os.path.isabs(path)
            or os.path.splitdrive(path)[0]):
        return None, "outside_out_dir"
    base = os.path.normpath(os.path.abspath(out_dir))
    target = os.path.normpath(os.path.join(base, path))
    if not _below(base, target):
        return None, "outside_out_dir"

    current = base
    if _is_link(current):
        return None, "symlink_or_junction"
    for part in os.path.relpath(target, base).split(os.sep):
        current = os.path.join(current, part)
        if _is_link(current):
            return None, "symlink_or_junction"
    real = os.path.realpath(target)
    if not _below(os.path.realpath(base), real):
        return None, "outside_out_dir"
    try:
        info = os.stat(target)
    except (OSError, ValueError):
        return None, "missing"
    if not stat.S_ISREG(info.st_mode):
        return None, "not_a_file"
    if info.st_nlink > 1:
        return None, "hard_link"
    if kind == "image":
        if os.path.splitext(target)[1][1:].lower() not in IMAGE_ARTIFACT_EXTENSIONS:
            return None, "bad_extension"
        if info.st_size > IMAGE_ARTIFACT_MAX_BYTES:
            return None, "too_large"
    elif kind == "text" and info.st_size > TEXT_ARTIFACT_MAX_BYTES:
        return None, "too_large"
    return real, None


# ── describe (Autona's PluginRunner.Describe and PluginRegistry.JudgeDescribe) ──

def describe_facts(folder, entry, dev, environ=None, timeout=DESCRIBE_TIMEOUT_SECONDS,
                   poll=0.05):
    """Run ``<entry> describe`` as Autona does (cwd the plugin folder, stdin
    and stderr discarded, NORMAL priority, the describe environment) and
    report the facts: launched, entry_missing, timed_out, exit_code,
    stdout_lines (the non-empty ones), objects (the JSON-object lines: the
    first 32, and the first describe answer past them), seconds."""
    facts = {"launched": False, "entry_missing": False, "timed_out": False,
             "exit_code": None, "stdout_lines": 0, "objects": [], "seconds": None}
    try:
        argv = [*entry_argv(folder, entry, dev), "describe"]
    except ValueError:
        return facts
    if not dev and not os.path.isfile(argv[0]):
        facts["entry_missing"] = True
        return facts
    env = build_environment(os.environ if environ is None else environ, folder)
    argv = _resolve_command(argv, env)
    started = time.monotonic()
    try:
        proc, job = _spawn(argv, folder, env, _NORMAL, None, subprocess.DEVNULL,
                           subprocess.DEVNULL)
    except _LaunchError as problem:
        facts["entry_missing"] = problem.entry_missing
        return facts
    facts["launched"] = True
    splitter = _Lines()
    answered = {"done": False}

    def take(line, size):
        if size > LINE_MAX_BYTES:
            facts["stdout_lines"] += 1
            return
        text = _decode(line).strip()
        if not text:
            return
        facts["stdout_lines"] += 1
        obj = _loads(text)
        if not isinstance(obj, dict):
            return
        answer = obj.get("type") == "describe" and not answered["done"]
        answered["done"] = answered["done"] or answer
        if len(facts["objects"]) < 32 or answer:
            facts["objects"].append(obj)

    def read():
        fd = proc.stdout.fileno()
        try:
            while True:
                data = os.read(fd, 65536)
                if not data:
                    break
                for item in splitter.feed(data):
                    take(*item)
        except (OSError, ValueError):
            pass
        tail = splitter.flush()
        if tail is not None:
            take(*tail)

    reader = threading.Thread(target=read, name="autona-plugin-describe", daemon=True)
    reader.start()
    deadline = started + timeout
    while proc.poll() is None:
        if time.monotonic() >= deadline:
            facts["timed_out"] = True
            _kill(proc, job)
            break
        time.sleep(poll)
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        pass
    if job is not None:
        job.close()
    reader.join(2.0)
    facts["seconds"] = round(time.monotonic() - started, 3)
    facts["exit_code"] = None if facts["timed_out"] else proc.returncode
    try:
        proc.stdout.close()
    except OSError:
        pass
    return facts


def judge_describe(facts, manifest):
    """Autona's judgement of describe's facts against the manifest ->
    {"failure": a DESCRIBE_FAILURES code or None, "protocol": the one to
    use, "answer": the answer or None, "detail"}."""
    def verdict(failure=None, answer=None, protocol=None, detail=""):
        return {"failure": failure, "answer": answer, "protocol": protocol,
                "detail": detail}

    if not facts["launched"]:
        return verdict("launch_failed", detail="entry missing" if facts["entry_missing"]
                       else "")
    if facts["timed_out"]:
        return verdict("timed_out")
    if facts["exit_code"] != 0:
        return verdict("nonzero_exit", detail="no exit code" if facts["exit_code"] is None
                       else f"exit code {facts['exit_code']}")
    if facts["stdout_lines"] == 0:
        return verdict("no_line")
    if not facts["objects"]:
        return verdict("not_json")
    line = next((obj for obj in facts["objects"] if obj.get("type") == "describe"), None)
    if line is None:
        return verdict("type_not_describe")
    protocol = line.get("protocol")
    if not (isinstance(protocol, list) and protocol and all(_is_int(i) for i in protocol)):
        return verdict("protocol_missing")
    answer = {"protocol": [int(item) for item in protocol],
              "id": line.get("id") if isinstance(line.get("id"), str) else "",
              "version": line.get("version") if isinstance(line.get("version"), str) else ""}
    if answer["id"] != manifest.get("id"):
        return verdict("id_mismatch", answer, detail=answer["id"])
    if answer["version"] != manifest.get("version"):
        return verdict("version_mismatch", answer, detail=answer["version"])
    common = set(answer["protocol"]) & set(HOST_SPEAKS_PROTOCOLS)
    if not common:
        return verdict("no_common_protocol", answer)
    return verdict(answer=answer, protocol=max(common))


# ── the manifest's check (Autona's PluginRegistry.Check) ──

_TOP_KEYS = frozenset({
    "manifest", "id", "version", "name", "description", "author", "homepage", "funding",
    "icon", "license", "min_autona", "protocol", "platforms", "entry", "update",
    "permissions", "settings", "jobs"})
_FILES_SCOPES = ("inputs", "data", "user", "anywhere")
_API_USES = ("none", "read", "write")
_ENTRY_HEAD = re.compile(
    r"^(?!@)(?![A-Za-z]:)(?![/\\])(?!(.*[/\\])?\.\.([/\\]|$))"
    r"[^:*?\"<>|\u0000-\u001f]+$")
_HOST_NAME = re.compile(
    r"^(\*|[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*)$")
_HTTPS_URL = re.compile(r"^https://[^\s@/]+(/[^\s]*)?$")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _version(text):
    if not isinstance(text, str) or not re.fullmatch(VERSION_PATTERN, text):
        return None
    try:
        return tuple(int(part) for part in text.split("."))
    except ValueError:
        return None


def _is_int(value):
    if isinstance(value, bool):
        return False
    return isinstance(value, int) or (isinstance(value, float) and value.is_integer())


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_text(value, max_length=None, min_length=1):
    def good(text):
        return (isinstance(text, str) and len(text) >= min_length
                and (max_length is None or len(text) <= max_length))
    if isinstance(value, str):
        return good(value)
    if isinstance(value, dict):
        return bool(value) and all(isinstance(key, str) and re.fullmatch(LOCALE_PATTERN, key)
                                   and good(text) for key, text in value.items())
    return False


def _is_unique_list(value):
    if not isinstance(value, list):
        return False
    seen = []
    for item in value:
        if item in seen:
            return False
        seen.append(item)
    return True


def _inside(base, path):
    try:
        real = os.path.normcase(os.path.realpath(path))
        root = os.path.normcase(os.path.realpath(base))
        return os.path.commonpath([real, root]) == root and real != root
    except (OSError, ValueError):
        return False


def _device_name(part):
    return re.fullmatch(r"(con|nul|aux|prn|com[1-9]|lpt[1-9])", part) is not None


class _Found:
    def __init__(self):
        self.items = []

    def add(self, code, scope, path="", job=None, detail=""):
        self.items.append({"code": code, "scope": scope, "path": path, "job": job,
                           "detail": detail})

    def plugin(self, code, path="", detail=""):
        self.add(code, "plugin", path, None, detail)

    def job(self, code, job, path="", detail=""):
        self.add(code, "job", path, job, detail)

    def warning(self, code, path="", detail=""):
        self.add(code, "warning", path, None, detail)


def read_manifest_file(folder):
    """(the parsed plugin.json, None) or (None, the manifest_unreadable
    problem)."""
    def unreadable(detail):
        return {"code": "manifest_unreadable", "scope": "plugin", "path": MANIFEST_FILE,
                "job": None, "detail": detail}
    try:
        with open(os.path.join(folder, MANIFEST_FILE), "rb") as file:
            data = file.read(MANIFEST_MAX_BYTES + 1)
    except OSError as problem:
        return None, unreadable(f"cannot read {MANIFEST_FILE}: {type(problem).__name__}")
    if len(data) > MANIFEST_MAX_BYTES:
        return None, unreadable(f"over {MANIFEST_MAX_BYTES} bytes")
    try:
        return json.loads(data.decode("utf-8-sig")), None
    except UnicodeDecodeError:
        return None, unreadable("not UTF-8")
    except ValueError as problem:
        return None, unreadable(f"not JSON: {getattr(problem, 'msg', problem)}")
    except RecursionError:
        return None, unreadable("nested too deep")


def check_manifest(raw, folder, dev=False, host_version=None, environ=None, source=None):
    """Every problem Autona finds in the manifest ``raw`` of ``folder``
    (contract_1.json manifest_checks), as dicts {code, scope, path, job,
    detail}. Without ``dev`` the folder is an installed one (its name is the
    id, the entry is an .exe). ``source``: where an installed folder came
    from -- None (not known: Autona then does not hold ``taguyan.`` against
    it) or "manual" (a zip installed by hand: ``taguyan.`` is reserved).
    ``host_version``: min_autona_newer against it (skipped when None).
    id_taken needs two folders and is never reported here."""
    if source not in (None, "manual"):
        raise ValueError("source: None or 'manual'")
    found = _Found()
    if not isinstance(raw, dict):
        found.plugin("manifest_unreadable", MANIFEST_FILE, "not a JSON object")
        return found.items
    for key in raw:
        if key not in _TOP_KEYS:
            found.warning("unknown_key", str(key))
    version = raw.get("manifest")
    if not _is_int(version):
        found.plugin("invalid_field", "manifest")
        return found.items
    if int(version) not in HOST_READS_MANIFESTS:
        found.plugin("manifest_version_unknown", "manifest", str(version))
        return found.items
    plugin_id = raw.get("id")
    if not (isinstance(plugin_id, str) and re.fullmatch(PLUGIN_ID_PATTERN, plugin_id)
            and len(plugin_id) <= PLUGIN_ID_MAX_LENGTH):
        found.plugin("invalid_field", "id")
        return found.items
    if any(_device_name(part) for part in plugin_id.split(".")):
        found.plugin("invalid_field", "id", "a Windows device name")
        return found.items
    if not dev and os.path.basename(os.path.normpath(folder)) != plugin_id:
        found.plugin("id_folder_mismatch", "id")
    if plugin_id.startswith(RESERVED_ID_PREFIX) and not dev and source == "manual":
        found.plugin("id_reserved", "id")
    which = _which(environ)
    _check_top(raw, folder, dev, host_version, which, found)
    _check_fields(raw.get("settings", []), "settings", found, None)
    jobs = raw.get("jobs")
    if isinstance(jobs, list):
        for index, job in enumerate(jobs):
            if isinstance(job, dict):
                _check_job(job, f"jobs[{index}]", found)
    return found.items


def _which(environ):
    path = None if environ is None else (environ.get("PATH") or environ.get("Path") or "")
    return lambda name: shutil.which(name, path=path)


def _check_top(raw, folder, dev, host_version, which, found):
    invalid = "invalid_field"
    if _version(raw.get("version")) is None:
        found.plugin(invalid, "version")
    if not _is_text(raw.get("name"), NAME_MAX_LENGTH):
        found.plugin(invalid, "name")
    if "description" in raw and not _is_text(raw["description"], DESCRIPTION_MAX_LENGTH, 0):
        found.plugin(invalid, "description")
    if not (isinstance(raw.get("author"), str) and raw["author"]):
        found.plugin(invalid, "author")
    for key in ("homepage", "funding"):
        if key in raw and not (isinstance(raw[key], str) and len(raw[key]) <= URL_MAX_LENGTH
                               and _HTTPS_URL.fullmatch(raw[key])):
            found.plugin(invalid, key)
    if not (isinstance(raw.get("license"), str) and raw["license"]):
        found.plugin(invalid, "license")
    minimum = _version(raw.get("min_autona"))
    if minimum is None:
        found.plugin(invalid, "min_autona")
    elif host_version is not None:
        host = _version(host_version)
        if host is not None and minimum > host:
            found.plugin("min_autona_newer", "min_autona", raw["min_autona"])

    protocol = raw.get("protocol")
    if not (_is_unique_list(protocol) and protocol
            and all(_is_int(item) and item >= 1 for item in protocol)):
        found.plugin(invalid, "protocol")
    elif not set(protocol) & set(HOST_SPEAKS_PROTOCOLS):
        found.plugin("protocol_unsupported", "protocol")

    platforms = raw.get("platforms")
    if not (_is_unique_list(platforms) and platforms and all(
            isinstance(item, str) and re.fullmatch(PLATFORM_PATTERN, item)
            for item in platforms)):
        found.plugin(invalid, "platforms")
    elif HOST_PLATFORM not in platforms:
        found.plugin("platform_unsupported", "platforms")

    _check_entry(raw.get("entry"), folder, dev, which, found)

    if "icon" in raw:
        if not (isinstance(raw["icon"], str) and raw["icon"]):
            found.plugin(invalid, "icon")
        else:
            why = _icon_problem(folder, raw["icon"])
            if why:
                found.warning("icon_invalid", "icon", why)

    if "update" in raw:
        update = raw["update"]
        if not (isinstance(update, dict) and isinstance(update.get("github"), str)
                and re.fullmatch(GITHUB_REPO_PATTERN, update["github"])):
            found.plugin(invalid, "update")

    permissions = raw.get("permissions")
    if not isinstance(permissions, dict):
        found.plugin(invalid, "permissions")
    else:
        network = permissions.get("network")
        if not (_is_unique_list(network) and all(
                isinstance(host, str) and _HOST_NAME.fullmatch(host) for host in network)):
            found.plugin(invalid, "permissions.network")
        if permissions.get("files") not in _FILES_SCOPES:
            found.plugin(invalid, "permissions.files")
        if not isinstance(permissions.get("spawn"), bool):
            found.plugin(invalid, "permissions.spawn")
        if permissions.get("autona_api") not in _API_USES:
            found.plugin(invalid, "permissions.autona_api")
        if "note" in permissions and not _is_text(permissions["note"]):
            found.plugin(invalid, "permissions.note")

    if "settings" in raw and not isinstance(raw["settings"], list):
        found.plugin(invalid, "settings")
    jobs = raw.get("jobs")
    if not (isinstance(jobs, list) and jobs and all(isinstance(job, dict) for job in jobs)):
        found.plugin(invalid, "jobs")
    else:
        ids = [job.get("id") for job in jobs]
        if len(ids) != len(set(map(repr, ids))):
            found.plugin(invalid, "jobs", "a job id twice")


def _check_entry(entry, folder, dev, which, found):
    if not (isinstance(entry, list) and entry and all(isinstance(word, str) for word in entry)):
        found.plugin("invalid_field", "entry")
        return
    head = entry[0]
    if head.startswith(RESERVED_ENTRY_PREFIX):
        found.plugin("entry_reserved", "entry[0]")
        return
    if not _ENTRY_HEAD.fullmatch(head):
        found.plugin("invalid_field", "entry[0]")
        return
    path = os.path.join(folder, head)
    bare = not re.search(r"[/\\]", head)
    if dev and bare and not os.path.isfile(path):
        if not which(head):
            found.plugin("entry_missing", "entry[0]", "not in the folder nor on PATH")
        return
    if not _inside(folder, path):
        found.plugin("entry_outside", "entry[0]")
    elif not os.path.isfile(path):
        found.plugin("entry_missing", "entry[0]")
    elif not dev and not head.lower().endswith(".exe"):
        found.plugin("entry_missing", "entry[0]", "not an .exe")


def _icon_problem(folder, icon):
    if not icon.lower().endswith(".png") or not _ENTRY_HEAD.fullmatch(icon):
        return "not a relative .png path"
    path = os.path.join(folder, icon)
    if not _inside(folder, path):
        return "outside the folder"
    try:
        if not os.path.isfile(path):
            return "missing"
        if os.path.getsize(path) > ICON_MAX_BYTES:
            return f"over {ICON_MAX_BYTES} bytes"
        with open(path, "rb") as file:
            if file.read(len(_PNG_SIGNATURE)) != _PNG_SIGNATURE:
                return "not a png"
    except OSError:
        return "unreadable"
    return ""


def _check_fields(fields, where, found, job):
    """``params`` (``job`` = (job id, path)) or ``settings`` (job None:
    every problem is the plugin's). Returns [(key, type, required)]."""
    if not isinstance(fields, list):
        return []
    base = where if job is None else f"{job[1]}.params"
    job_id = None if job is None else job[0]

    def report(code, path, detail=""):
        if job is None:
            found.plugin(code, path, detail)
        else:
            found.job(code, job_id, path, detail)

    invalid = "invalid_field" if job is None else "invalid_job_field"
    built = []
    keys = set()
    for index, item in enumerate(fields):
        path = f"{base}[{index}]"
        if not isinstance(item, dict):
            report(invalid, path)
            continue
        key, kind = item.get("key"), item.get("type")
        if not (isinstance(key, str) and re.fullmatch(FIELD_KEY_PATTERN, key)):
            report(invalid, f"{path}.key")
        elif key in keys:
            report("key_duplicate", f"{path}.key", key)
        else:
            keys.add(key)
        if not (isinstance(kind, str) and re.fullmatch(NAME_PATTERN, kind)):
            report(invalid, f"{path}.type")
            kind = None
        if not _is_text(item.get("label")):
            report(invalid, f"{path}.label")
        if "required" in item and not isinstance(item["required"], bool):
            report(invalid, f"{path}.required")
        if "help" in item and not _is_text(item["help"]):
            report(invalid, f"{path}.help")
        if "max_length" in item and not (_is_int(item["max_length"])
                                         and item["max_length"] >= 1):
            report(invalid, f"{path}.max_length")
        for extra in ("min", "max"):
            if extra in item and not _is_number(item[extra]):
                report(invalid, f"{path}.{extra}")
        if "step" in item and not (_is_number(item["step"]) and item["step"] > 0):
            report(invalid, f"{path}.step")
        for extra, owner in (("options", "select"), ("extensions", "file")):
            if extra in item and not isinstance(item[extra], list) and kind != owner:
                report(invalid, f"{path}.{extra}")
        if kind is not None:
            _check_type(item, kind, path, where, report, invalid)
        built.append((key if isinstance(key, str) else "", kind or "",
                      item.get("required") is True))
    return built


def _check_type(item, kind, path, where, report, invalid):
    if kind not in VALUE_FIELD_TYPES and kind not in SOURCE_FIELD_TYPES:
        report("field_type_unknown", f"{path}.type", kind)
        return
    if kind == "secret" and where == "params":
        report("secret_outside_settings", f"{path}.type")
        return
    if kind in SOURCE_FIELD_TYPES:
        if where == "settings":
            report("source_in_settings", f"{path}.type", kind)
        return
    has = "default" in item
    default = item.get("default")
    bad = "default_invalid"
    if kind in ("text", "textarea"):
        limit = item.get("max_length")
        if _is_int(limit) and limit > TEXT_MAX_LENGTH["cap"]:
            report(invalid, f"{path}.max_length")
        if has:
            if not _is_int(limit) or limit < 1:
                limit = TEXT_MAX_LENGTH[f"{kind}_default"]
            if not isinstance(default, str) or len(default) > limit:
                report(bad, f"{path}.default")
    elif kind == "number":
        if has and (not _is_number(default)
                    or (_is_number(item.get("min")) and default < item["min"])
                    or (_is_number(item.get("max")) and default > item["max"])):
            report(bad, f"{path}.default")
    elif kind == "bool":
        if has and not isinstance(default, bool):
            report(bad, f"{path}.default")
    elif kind == "select":
        options = item.get("options")
        good = (isinstance(options, list) and 1 <= len(options) <= SELECT_MAX_OPTIONS and all(
            isinstance(option, dict) and isinstance(option.get("value"), str)
            and option["value"] and _is_text(option.get("label")) for option in options))
        if not good:
            report(invalid, f"{path}.options")
        elif has and default not in [option["value"] for option in options]:
            report(bad, f"{path}.default")
    elif kind == "file":
        extensions = item.get("extensions")
        if not (_is_unique_list(extensions) and extensions
                and len(extensions) <= FILE_EXTENSIONS_MAX and all(
                isinstance(ext, str) and re.fullmatch(FILE_EXTENSION_PATTERN, ext)
                for ext in extensions)):
            report(invalid, f"{path}.extensions")
        if has and not isinstance(default, str):
            report(bad, f"{path}.default")
    elif kind == "folder":
        if has and not isinstance(default, str):
            report(bad, f"{path}.default")
    elif kind == "secret":
        if has:
            report(invalid, f"{path}.default", "a secret has no default")


def _check_job(raw, path, found):
    job_id = raw.get("id")
    name = job_id if isinstance(job_id, str) else path
    invalid = "invalid_job_field"

    def problem(code, where="", detail=""):
        found.job(code, name, f"{path}.{where}" if where else path, detail)

    if not (isinstance(job_id, str) and re.fullmatch(JOB_ID_PATTERN, job_id)):
        problem(invalid, "id")
    if not _is_text(raw.get("title")):
        problem(invalid, "title")
    lane = raw.get("lane")
    if lane not in LANES:
        problem(invalid, "lane")
    actions = raw.get("actions", [])
    hooks = raw.get("hooks", [])
    if not isinstance(actions, list):
        problem(invalid, "actions")
        actions = []
    if not isinstance(hooks, list):
        problem(invalid, "hooks")
        hooks = []

    timeout = raw.get("timeout_seconds")
    if not _is_int(timeout):
        problem(invalid, "timeout_seconds")
    else:
        low, high = LIGHT_TIMEOUT_SECONDS if lane == "light" else HEAVY_TIMEOUT_SECONDS
        if not low <= timeout <= high:
            problem("timeout_out_of_range", "timeout_seconds", str(timeout))
    memory = raw.get("memory_mb")
    if "memory_mb" in raw and not _is_int(memory):
        problem(invalid, "memory_mb")
    elif lane == "light":
        if memory is None:
            problem(invalid, "memory_mb", "a light job needs memory_mb")
        else:
            low, high = LIGHT_MEMORY_MB
            if hooks:
                high = min(high, HOOK_JOB_MEMORY_MB_MAX)
            if not low <= memory <= high:
                problem("memory_out_of_range", "memory_mb", str(memory))
    if "manual" in raw and not isinstance(raw["manual"], bool):
        problem(invalid, "manual")
    if "params" in raw and not isinstance(raw["params"], list):
        problem(invalid, "params")
    params = _check_fields(raw.get("params", []), "params", found, (name, path))

    targets = []
    for index, action in enumerate(actions):
        where = f"actions[{index}]"
        if not (isinstance(action, dict) and isinstance(action.get("target"), str)
                and re.fullmatch(NAME_PATTERN, action["target"])
                and _is_text(action.get("label"))):
            problem(invalid, where)
            continue
        if action["target"] not in ACTION_FILLS:
            problem("action_target_unknown", f"{where}.target", action["target"])
        targets.append(action["target"])
    events = []
    seen = set()
    for index, hook in enumerate(hooks):
        where = f"hooks[{index}]"
        if not (isinstance(hook, dict) and isinstance(hook.get("on"), str)
                and re.fullmatch(NAME_PATTERN, hook["on"]) and _is_text(hook.get("label"))):
            problem(invalid, where)
            continue
        if hook["on"] not in HOOK_FILLS:
            problem("hook_event_unknown", f"{where}.on", hook["on"])
        if hook["on"] in seen:
            problem("hook_duplicate", f"{where}.on", hook["on"])
        seen.add(hook["on"])
        events.append(hook["on"])

    if (targets or events) and lane != "light":
        problem("trigger_needs_light_lane", "lane")
    manual = raw.get("manual", True)
    if any(kind in CONTEXT_ONLY_TYPES for _key, kind, _req in params) and manual is not False:
        problem("manual_with_context", "manual")
    _check_fills(params, targets, events, problem)
    if events and isinstance(raw.get("params"), list):
        for index, item in enumerate(raw["params"]):
            if (isinstance(item, dict) and item.get("required") is True
                    and isinstance(item.get("type"), str)
                    and item["type"] not in SOURCE_FIELD_TYPES and "default" not in item):
                problem("hook_param_needs_default", f"params[{index}].default",
                        str(item.get("key")))
    if manual is False and not targets and not events:
        found.warning("job_unreachable", path, name)


def _check_fills(params, targets, events, problem):
    triggers = [(f"actions[{i}]", ACTION_FILLS[t]) for i, t in enumerate(targets)
                if t in ACTION_FILLS]
    triggers += [(f"hooks[{i}]", HOOK_FILLS[e]) for i, e in enumerate(events)
                 if e in HOOK_FILLS]
    images = ("result_image", "thumbnail_image")
    for where, fills in triggers:
        count = sum(1 for _key, kind, _req in params if kind == fills)
        if count != 1:
            problem("fill_count", where, f"{count} fields of {fills}")
        for key, kind, required in params:
            if kind == fills:
                continue
            if kind in CONTEXT_ONLY_TYPES or (kind in images and required):
                problem("trigger_unfillable", where, f"cannot fill {key} ({kind})")


def plugin_usable(problems):
    return not any(problem["scope"] == "plugin" for problem in problems)


def usable_jobs(raw, problems):
    if not plugin_usable(problems):
        return []
    bad = {problem["job"] for problem in problems if problem["scope"] == "job"}
    return [job.get("id") for job in raw.get("jobs") or []
            if isinstance(job, dict) and job.get("id") not in bad]


# ── check <folder> ──

def _skipped(name):
    return name.startswith(".") or name in SKIPPED_FOLDERS


def _walk(folder):
    """Every name under the folder as its path relative to it, without the
    folders _skipped leaves out (and what is in them)."""
    for root, dirs, files in os.walk(folder):
        dirs[:] = [name for name in dirs if not _skipped(name)]
        for name in dirs + files:
            yield os.path.relpath(os.path.join(root, name), folder)


def longest_path(folder, plugin_id):
    """The longest path the folder makes once installed, relative to the
    Autona folder (``plugins\\<id>\\...``), and the room left for the Autona
    folder's own path within INSTALLED_PATH_MAX_LENGTH. Only the plugin
    folder is measured: what the plugin makes under user_data is not."""
    longest = ""
    for rel in _walk(folder):
        if len(rel) > len(longest):
            longest = rel
    installed = "\\".join(["plugins", plugin_id or os.path.basename(folder),
                           *longest.split(os.sep)]) if longest else ""
    room = INSTALLED_PATH_MAX_LENGTH - len(installed) - 1
    return {"path": installed, "length": len(installed), "autona_dir_room": room}


def non_ascii_names(folder):
    """Paths under the folder whose names are not ASCII (Autona refuses a
    zip entry name that is not ASCII)."""
    return [rel for rel in _walk(folder) if not os.path.basename(rel).isascii()]


# Autona's ZipSafety name rules (C.1): a zip entry is refused for these.
_BAD_CHARS = frozenset('<>:"|?*')
_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"]
    + [f"COM{digit}" for digit in "0123456789"]
    + [f"LPT{digit}" for digit in "0123456789"]
    + [f"COM{digit}" for digit in "¹²³"]
    + [f"LPT{digit}" for digit in "¹²³"])
_SHORT_NAME = re.compile(r"~[0-9]")


def zip_name_problem(name, ascii_only=True):
    """A zip entry's name as Autona's ZipSafety.NameProblem sees it: None,
    or unsafe_path (absolute, a drive, ``..``, a ``:``), bad_name (an empty
    or ``.`` part, a refused or control character, a trailing dot or space,
    an NTFS short name's ``~<digit>``), reserved_name (a device name, with
    or without an extension) or, ``ascii_only``, not_ascii. ``\\`` counts
    as ``/``; a folder's trailing ``/`` is dropped."""
    if not isinstance(name, str) or not name:
        return "bad_name"
    normalised = name.replace("\\", "/")
    if normalised.startswith("/") or ":" in normalised:
        return "unsafe_path"
    if normalised.endswith("/"):
        normalised = normalised[:-1]
    parts = normalised.split("/")
    if ".." in parts:
        return "unsafe_path"
    for part in parts:
        if part in ("", "."):
            return "bad_name"
        if any(ch in _BAD_CHARS or ord(ch) < 32 for ch in part):
            return "bad_name"
        if part[-1] in ". " or _SHORT_NAME.search(part):
            return "bad_name"
        if part.split(".")[0].upper() in _RESERVED_NAMES:
            return "reserved_name"
    if ascii_only and any(ord(ch) > 0x7e for ch in normalised):
        return "not_ascii"
    return None


def bad_names(folder):
    """[{path, code}] of the names under the folder a zip of it could not
    hold (zip_name_problem but not_ascii: non_ascii_names reports those)."""
    found = []
    for rel in _walk(folder):
        code = zip_name_problem(rel.replace(os.sep, "/"), ascii_only=False)
        if code is not None:
            found.append({"path": rel, "code": code})
    return found


def check_folder(folder, dev=False, host_version=None, environ=None,
                 describe_timeout=DESCRIBE_TIMEOUT_SECONDS, try_non_ascii=True, source=None):
    """``check <folder>`` -> a dict: problems (check_manifest's; ``source``
    as there), describe (its facts and judgement, in place), longest_path,
    non_ascii_names, bad_names, non_ascii (describe again from a copy under
    a non-ASCII folder, with TEMP / TMP / APPDATA / LOCALAPPDATA there too;
    None when not tried or the copy failed), notes (the fake host's own
    warnings) and ok. It runs the entry (``describe``, twice). An installed
    folder (not ``dev``) is not ok with fewer than PATH_ROOM_MIN characters
    left for the Autona folder, a name that is not ASCII or a bad name; a
    dev folder only notes them. Folders named with a leading dot and
    __pycache__ are not looked at (leave them out of the zip)."""
    folder = os.path.abspath(folder)
    environ = dict(os.environ if environ is None else environ)
    report = {"folder": folder, "dev": dev, "problems": [], "describe": None,
              "longest_path": None, "non_ascii_names": [], "bad_names": [], "non_ascii": None,
              "notes": [], "ok": False}
    notes = report["notes"]
    raw, unreadable = read_manifest_file(folder)
    report["problems"] = ([unreadable] if unreadable else
                          check_manifest(raw, folder, dev=dev, host_version=host_version,
                                         environ=environ, source=source))
    usable = plugin_usable(report["problems"])
    if isinstance(raw, dict):
        plugin_id = raw.get("id") if isinstance(raw.get("id"), str) else None
        report["longest_path"] = longest_path(folder, plugin_id)
        report["non_ascii_names"] = non_ascii_names(folder)
        report["bad_names"] = bad_names(folder)
        room = report["longest_path"]["autona_dir_room"]
        if room < PATH_ROOM_WARN:
            fails = "" if dev else f"; fewer than {PATH_ROOM_MIN} fails the check"
            notes.append(f"the longest installed path leaves {room} characters for the Autona "
                         f"folder (fewer than {PATH_ROOM_WARN}{fails}; paths the plugin makes "
                         f"under user_data are not counted)")
        for name in report["non_ascii_names"]:
            notes.append(f"a name that is not ASCII (a zip refuses it): {name}")
        for item in report["bad_names"]:
            notes.append(f"a name a zip refuses ({item['code']}): {item['path']}")
        if (plugin_id and plugin_id.startswith(RESERVED_ID_PREFIX)
                and (dev or source != "manual")):
            notes.append(f"the id starts with {RESERVED_ID_PREFIX}: Autona reserves it for its "
                         f"own plugins (a zip installed by hand is refused; see --source "
                         f"manual)")
    if usable:
        entry = raw["entry"]
        facts = describe_facts(folder, entry, dev, environ, describe_timeout)
        report["describe"] = dict(facts, **judge_describe(facts, raw))
        if facts["seconds"] is not None and facts["seconds"] > DESCRIBE_SLOW_SECONDS:
            notes.append(f"describe took {facts['seconds']} s (keep it light: Autona waits "
                         f"{DESCRIBE_TIMEOUT_SECONDS} s)")
        if try_non_ascii:
            report["non_ascii"] = _describe_non_ascii(folder, entry, dev, environ, raw,
                                                      describe_timeout, notes)
    blocking = [p for p in report["problems"] if p["scope"] != "warning"]
    installed_ok = dev or (
        (report["longest_path"] is None
         or report["longest_path"]["autona_dir_room"] >= PATH_ROOM_MIN)
        and not report["non_ascii_names"] and not report["bad_names"])
    report["ok"] = bool(
        usable and not blocking and report["describe"]
        and report["describe"]["failure"] is None
        and (report["non_ascii"] is None or report["non_ascii"]["failure"] is None)
        and installed_ok)
    return report


def _describe_non_ascii(folder, entry, dev, environ, raw, timeout, notes):
    base = tempfile.mkdtemp(prefix="autona-plugin-check-")
    try:
        parent = os.path.join(base, NON_ASCII_FOLDER)
        temp = os.path.join(parent, "Temp テンポラリ")
        os.makedirs(temp)
        copy = os.path.join(parent, os.path.basename(folder))
        keep = os.path.normcase(re.split(r"[/\\]", entry[0])[0]) if entry else ""

        def ignore(where, names):
            # A dev tree's .venv / .git / caches stay behind (but the folder
            # the entry starts in, a .venv's python, say).
            if not dev:
                return []
            top = os.path.normcase(os.path.abspath(where)) == os.path.normcase(folder)
            return [name for name in names if _skipped(name)
                    and os.path.isdir(os.path.join(where, name))
                    and not (top and os.path.normcase(name) == keep)]

        shutil.copytree(folder, copy, symlinks=True, ignore=ignore)
        env = dict(environ)
        for name in ("TEMP", "TMP", "APPDATA", "LOCALAPPDATA"):
            for key in [k for k in env if k.upper() == name]:
                del env[key]
            env[name] = temp
        facts = describe_facts(copy, entry, dev, env, timeout)
        return dict(facts, folder=copy, **judge_describe(facts, raw))
    except (OSError, shutil.Error) as problem:
        notes.append(f"describe from a non-ASCII folder was not tried: the copy failed "
                     f"({type(problem).__name__}: {problem})")
        return None
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ── run <folder> <job> ──

class Refused(Exception):
    """The fake host does not start the run: ``reason`` (contract_1.json
    refusals: invalid_request, needs_settings, incompatible, job_unusable,
    not_found) and ``detail``."""

    def __init__(self, reason, detail=""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


_IMAGE_EXTENSIONS = {".png": ".png", ".jpg": ".jpg", ".jpeg": ".jpg"}


def _new_run_folder(work):
    import random
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    for _ in range(50):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        run_id = f"{stamp}-{''.join(random.choice(alphabet) for _ in range(4))}"
        folder = os.path.join(work, run_id)
        try:
            os.makedirs(folder)
        except FileExistsError:
            continue
        return run_id, folder
    raise FileExistsError("no free run id")


def _write_json(path, content):
    data = json.dumps(content, ensure_ascii=True, allow_nan=False, indent=2)
    with open(path, "w", encoding="utf-8") as file:
        file.write(data)


def _place_input(key, kind, source, inputs_dir):
    """inputs/<key><ext> from the file ``source`` (or an empty match log
    when None) -> request.json's item."""
    if source is None:
        data, extension = b"", ".jsonl"
    else:
        with open(source, "rb") as file:
            data = file.read()
        if kind in ("result_image", "thumbnail_image"):
            extension = _IMAGE_EXTENSIONS.get(os.path.splitext(source)[1].lower())
            if extension is None:
                raise Refused("invalid_request", f"{key}: an image is a png or a jpg")
        elif kind == "match_log":
            extension = ".jsonl"
            kept = []
            for line in data.decode("utf-8").split("\n"):
                obj = _loads(line.strip()) if line.strip() else None
                if isinstance(obj, dict):
                    kept.append(line.rstrip("\r"))
            data = "".join(line + "\n" for line in kept).encode("utf-8")
        else:
            extension = ".json"
            try:
                json.loads(data.decode("utf-8-sig"))
            except ValueError:
                raise Refused("invalid_request", f"{key}: not JSON") from None
    path = os.path.join(os.path.abspath(inputs_dir), key + extension)
    with open(path, "wb") as file:
        file.write(data)
    item = {"kind": kind, "format": INPUT_FORMATS[kind], "path": path}
    if kind in ("result_image", "thumbnail_image"):
        item["sha256"] = hashlib.sha256(data).hexdigest()
    return item


def _setting_value(field, text):
    kind = field.get("type")
    if kind == "number":
        try:
            number = float(text)
        except ValueError:
            raise Refused("invalid_request", f"{field.get('key')}: not a number") from None
        return int(number) if number.is_integer() and "." not in text else number
    if kind == "bool":
        if text.lower() in ("true", "1", "yes", "on"):
            return True
        if text.lower() in ("false", "0", "no", "off"):
            return False
        raise Refused("invalid_request", f"{field.get('key')}: not true / false")
    return text


# ── the values (Autona's PluginSettings.CheckFieldValue / _Empty and
#    PluginSecrets.CheckValue, the parts that need no picker) ──

def _max_length(field):
    default = TEXT_MAX_LENGTH["textarea_default" if field.get("type") == "textarea"
                              else "text_default"]
    limit = field.get("max_length")
    limit = limit if limit is not None else default
    return min(int(limit), TEXT_MAX_LENGTH["cap"])


def _is_absolute_local(path):
    """An absolute path on a drive of this PC: no UNC (``\\\\server``,
    ``//server``, ``\\\\?\\``), on Windows a drive letter."""
    if not isinstance(path, str) or not path or "\x00" in path:
        return False
    if path.startswith(("\\\\", "//")):
        return False
    if sys.platform == "win32":
        drive = ntpath.splitdrive(path)[0]
        return len(drive) == 2 and drive[1] == ":" and ntpath.isabs(path)
    return os.path.isabs(path)


def _finite_number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:  # an int too large for a float
        return False


def _on_step(field, value):
    step = field.get("step")
    if step is None or step <= 0:
        return True
    base = field.get("min") if field.get("min") is not None else 0
    try:
        ratio = (value - base) / step
        return abs(ratio - round(ratio)) <= 1e-9 * max(1.0, abs(ratio))
    except (OverflowError, ValueError):
        return False


def is_empty_value(field, value):
    """Unset: None, or an empty text (a text field's "" is no value)."""
    return value is None or (field.get("type") in ("text", "textarea") and value == "")


def check_field_value(field, value, stored=False):
    """(True, the value to keep) or (False, why) for a value field (its
    manifest dict) and a value that is not None, as Autona checks it. The
    fake host has no picker: a file / folder value is the absolute path
    itself, looked for on the disk unless ``stored`` (a kept value, a
    default). A secret or a source type is never a value here."""
    kind = field.get("type")
    if kind in ("text", "textarea"):
        if not isinstance(value, str):
            return False, "not a string"
        if len(value) > _max_length(field):
            return False, "too long"
        return True, value
    if kind == "number":
        if not _finite_number(value):
            return False, "not a number"
        if field.get("min") is not None and value < field["min"]:
            return False, "below min"
        if field.get("max") is not None and value > field["max"]:
            return False, "above max"
        if not _on_step(field, value):
            return False, "not on step"
        return True, value
    if kind == "bool":
        if not isinstance(value, bool):
            return False, "not a boolean"
        return True, value
    if kind == "select":
        if not isinstance(value, str) or value not in {
                option.get("value") for option in field.get("options") or []}:
            return False, "not an option"
        return True, value
    if kind in ("file", "folder"):
        if not isinstance(value, str) or not value:
            return False, "not a pick id"
        if not _is_absolute_local(value):
            return False, "not an absolute local path"
        path = os.path.normpath(value)
        if kind == "file":
            if os.path.splitext(path)[1][1:].lower() not in (field.get("extensions") or []):
                return False, "extension not allowed"
            if not stored and not os.path.isfile(path):
                return False, "not a file"
        elif not stored and not os.path.isdir(path):
            return False, "not a folder"
        return True, path
    if kind == "secret":
        return False, "a secret"
    return False, "not a value type"


def check_secret_value(value):
    """None for a secret value Autona keeps (a string of 1 to
    SECRET_MAX_LENGTH characters, no line break or other control
    character), else why not."""
    if not isinstance(value, str):
        return "a secret is a string"
    if not value:
        return "a secret is not empty (clear it instead)"
    if len(value) > SECRET_MAX_LENGTH:
        return f"a secret is at most {SECRET_MAX_LENGTH} characters"
    if any(unicodedata.category(mark) == "Cc" for mark in value):
        return "a secret has no line break or control character"
    return None


def _parse_trigger(text, job_id):
    if not text or text == "manual":
        return {"kind": "manual"}
    kind, _, name = text.partition(":")
    if kind == "action" and name in ACTION_FILLS:
        return {"kind": "action", "target": name}
    if kind == "hook" and name in HOOK_FILLS:
        return {"kind": "hook", "event": name, "hook": f"{job_id}/{name}"}
    raise Refused("invalid_request", f"unknown trigger: {text}")


def prepare_run(folder, job, params=None, inputs=None, settings=None, secrets=None,
                trigger=None, timeout=None, work=None, locale="ja", host_version=None,
                environ=None, api_url=None):
    """The run's job folder, inputs and request.json, as Autona's PluginJobs
    makes them for a dev plugin -> Prepared; Refused when Autona would not
    start it (a value that does not fit its field included: check_field_value,
    check_secret_value). ``params`` {key: value} (the form's values; a file
    / folder value an absolute path), ``inputs`` {key: file path},
    ``settings`` {key: value, or a text for a number / bool}, ``secrets``
    {key: value},
    ``trigger`` "manual" / "action:<target>" / "hook:<event>", ``timeout``
    replaces the job's limit, ``work`` the folder for the job folders
    (default <TEMP>/autona-plugin/<id>)."""
    folder = os.path.abspath(folder)
    environ = dict(os.environ if environ is None else environ)
    raw, unreadable = read_manifest_file(folder)
    problems = [unreadable] if unreadable else check_manifest(
        raw, folder, dev=True, host_version=host_version, environ=environ)
    if not plugin_usable(problems):
        raise Refused("incompatible", "; ".join(
            f"{p['code']} ({p['path']})" for p in problems if p["scope"] == "plugin"))
    spec = next((item for item in raw["jobs"] if item.get("id") == job), None)
    if spec is None:
        raise Refused("invalid_request", f"unknown job: {job}")
    if job not in usable_jobs(raw, problems):
        raise Refused("job_unusable", "; ".join(
            f"{p['code']} ({p['path']})" for p in problems
            if p["scope"] == "job" and p["job"] == job))
    trig = _parse_trigger(trigger, job)
    lane = spec["lane"]
    fills = None
    if trig["kind"] == "manual":
        if spec.get("manual", True) is False:
            raise Refused("invalid_request", "the job is not started by hand")
    else:
        if lane != "light":
            raise Refused("job_unusable", "an action or a hook needs lane light")
        if trig["kind"] == "action":
            if trig["target"] not in [a.get("target") for a in spec.get("actions", [])]:
                raise Refused("invalid_request", f"the job has no action {trig['target']}")
            fills = ACTION_FILLS[trig["target"]]
        else:
            if trig["event"] not in [h.get("on") for h in spec.get("hooks", [])]:
                raise Refused("invalid_request", f"the job has no hook {trig['event']}")
            fills = HOOK_FILLS[trig["event"]]

    fields = spec.get("params", [])
    value_fields = [f for f in fields if f["type"] in VALUE_FIELD_TYPES and f["type"] != "secret"]
    source_fields = [f for f in fields if f["type"] in SOURCE_FIELD_TYPES]
    params = dict(params or {})
    unknown = set(params) - {f["key"] for f in value_fields}
    if unknown:
        raise Refused("invalid_request", f"unknown param: {', '.join(sorted(unknown))}")
    values = {}
    for field in value_fields:  # Autona's check of a start's values
        key = field["key"]
        value = params.get(key)
        defaulted = is_empty_value(field, value)
        if defaulted:
            value = field.get("default")
        if is_empty_value(field, value):
            if field.get("required"):
                raise Refused("invalid_request", f"{key}: required")
            values[key] = value
            continue
        ok, kept = check_field_value(field, value, stored=defaulted)
        if not ok:
            raise Refused("invalid_request", f"{key}: {kept}")
        values[key] = kept

    setting_fields = raw.get("settings", [])
    settings = dict(settings or {})
    secrets = dict(secrets or {})
    known = {f["key"]: f for f in setting_fields}
    for key in settings:
        if key not in known or known[key]["type"] == "secret":
            raise Refused("invalid_request", f"not a setting: {key} (a secret: --secret)")
    for key, value in secrets.items():
        if key not in known or known[key]["type"] != "secret":
            raise Refused("invalid_request", f"not a secret setting: {key}")
        why = check_secret_value(value)
        if why is not None:
            raise Refused("invalid_request", f"{key}: {why}")
    setting_values, missing = {}, []
    for field in setting_fields:  # Autona's Save, then Get and Missing
        key = field["key"]
        if field["type"] == "secret":
            if key not in secrets and field.get("required"):
                missing.append(key)
            continue
        value = settings.get(key)
        if isinstance(value, str) and field["type"] in ("number", "bool"):
            value = _setting_value(field, value)
        if not is_empty_value(field, value):
            ok, value = check_field_value(field, value)
            if not ok:
                raise Refused("invalid_request", f"{key}: {value}")
        if is_empty_value(field, value):
            value = field.get("default")
        if field.get("required") and is_empty_value(field, value):
            missing.append(key)
        setting_values[key] = value
    if missing:
        raise Refused("needs_settings", ", ".join(missing))

    inputs = dict(inputs or {})
    source_kinds = {f["key"]: f["type"] for f in source_fields}
    for key in inputs:
        if key not in source_kinds:
            raise Refused("invalid_request", f"not an input of the job: {key}")
    for field in source_fields:
        key, kind = field["key"], field["type"]
        if fills is None and kind in CONTEXT_ONLY_TYPES:
            raise Refused("invalid_request", "a context-only input in a manual run")
        if kind == fills and key not in inputs:
            raise Refused("invalid_request", f"{key} is filled by the trigger: --input {key}=...")
        if kind == "state" and key not in inputs:
            raise Refused("invalid_request", f"{key}: the fake host has no live state: "
                          f"--input {key}=<state.json> (docs/plugins/inputs/state.json)")
        if fills is None and field.get("required") and key not in inputs:
            raise Refused("invalid_request", f"{key} is not picked: --input {key}=...")
        if fills is not None and kind != fills and kind in ("result_image", "thumbnail_image") \
                and field.get("required") and key not in inputs:
            raise Refused("invalid_request", f"{key} is not filled")

    plugin_id = raw["id"]
    protocol = max(set(raw["protocol"]) & set(HOST_SPEAKS_PROTOCOLS))
    if timeout is None:
        timeout = spec["timeout_seconds"]
        if trig["kind"] == "hook":
            timeout = min(timeout, HOOK_TIMEOUT_CAP_SECONDS)
    env = build_environment(
        environ, folder, protocol=protocol,
        secrets={ENV_SECRET_PREFIX + key.upper(): value for key, value in secrets.items()},
        api_url=api_url if raw["permissions"].get("autona_api") in ("read", "write") else None)
    argv = _resolve_command(_run_argv(folder, raw["entry"], True), env)

    work = os.path.abspath(work or os.path.join(tempfile.gettempdir(), "autona-plugin",
                                                plugin_id))
    os.makedirs(work, exist_ok=True)
    run_id, run_folder = _new_run_folder(work)
    inputs_dir = os.path.join(run_folder, INPUTS_DIR)
    out_dir = os.path.join(run_folder, OUT_DIR)
    data_dir = os.path.join(work, "data")
    request_path = os.path.join(run_folder, REQUEST_FILE)
    try:
        os.makedirs(inputs_dir)
        os.makedirs(out_dir)
        os.makedirs(data_dir, exist_ok=True)
        placed = {}
        for field in source_fields:
            key, kind = field["key"], field["type"]
            if key in inputs:
                placed[key] = _place_input(key, kind, inputs[key], inputs_dir)
            elif fills is not None and kind == "match_log":
                placed[key] = _place_input(key, kind, None, inputs_dir)  # an empty day
            else:
                placed[key] = None
        request = {
            "protocol": protocol, "plugin": plugin_id, "job": job, "run_id": run_id,
            "trigger": trig, "locale": "ja" if (locale or "").lower().startswith("ja") else "en",
            "params": values, "settings": setting_values, "inputs": placed,
            "out_dir": out_dir, "data_dir": data_dir,
            "host": {"name": "Autona", "version": host_version or raw["min_autona"]},
        }
        try:
            _write_json(request_path, request)
        except (TypeError, ValueError):
            raise Refused("invalid_request", "a value is not JSON") from None
    except BaseException:
        shutil.rmtree(run_folder, ignore_errors=True)  # no half-made job folder is left
        raise
    return Prepared(argv=[*argv, "run", request_path], cwd=run_folder, env=env, lane=lane,
                    timeout=timeout, memory_mb=spec.get("memory_mb"),
                    log_path=os.path.join(run_folder, LOG_FILE), request=request,
                    folder=run_folder)


def fake_run(folder, job, cancel_after=None, grace=CANCEL_GRACE_SECONDS, on_event=None,
             **options):
    """``run <folder> <job>``: prepare_run (``options`` are its keywords),
    then execute -> RunResult. Raises Refused."""
    prepared = prepare_run(folder, job, **options)
    return execute(prepared, cancel_after=cancel_after, grace=grace, on_event=on_event)


# ── the command line ──

def _pairs(items, what):
    pairs = {}
    for item in items or []:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise Refused("invalid_request", f"{what} {item!r}: KEY=VALUE")
        pairs[key] = value
    return pairs


def _describe_event(event, locale):
    kind = event["type"]
    if kind == "stage":
        where = f" ({event['index']}/{event['count']})" if event["index"] else ""
        return f"stage    {event['stage']}{where} {pick_text(event['label'], locale) or ''}"
    if kind == "progress":
        parts = [f"{name}={event[name]}" for name in ("stage", "fraction", "overall", "eta")
                 if event[name] is not None]
        return "progress " + " ".join(parts)
    if kind in ("log", "warning"):
        return f"{kind:<8} {pick_text(event['message'], locale)}"
    if kind == "done":
        lines = [f"done     {pick_text(event['summary'], locale) or ''}"]
        lines += [f"         artifact {a['kind']}: {a['path']}" for a in event["artifacts"]]
        return "\n".join(lines)
    if kind == "error":
        hint = pick_text(event["hint"], locale)
        return (f"error    {event['code']}: {pick_text(event['message'], locale)}"
                + (f" (hint: {hint})" if hint else ""))
    return "cancelled"


def _print_check(report):
    print(f"check {report['folder']}{' (dev)' if report['dev'] else ''}")
    for problem in report["problems"]:
        where = f" job {problem['job']}" if problem["job"] else ""
        detail = f": {problem['detail']}" if problem["detail"] else ""
        print(f"  [{problem['scope']}]{where} {problem['code']} at {problem['path']}{detail}")
        if problem["code"] == "entry_missing" and not report["dev"]:
            print("    (an installed plugin's entry is an .exe in the folder; for a dev "
                  "folder use --dev)")
    for name, item in (("describe", report["describe"]),
                       ("describe from a non-ASCII folder", report["non_ascii"])):
        if item is None:
            continue
        if item.get("failure"):
            print(f"  {name}: FAILED {item['failure']} {item.get('detail') or ''}".rstrip())
        else:
            print(f"  {name}: ok (protocol {item['protocol']}, {item.get('seconds')} s)")
    if report["longest_path"]:
        lp = report["longest_path"]
        print(f"  longest installed path: {lp['length']} characters; the Autona folder may be "
              f"up to {lp['autona_dir_room']} characters")
    for note in report["notes"]:
        print(f"  note: {note}")
    print("OK" if report["ok"] else "NOT OK")


def cli(argv=None):
    """The fake host's command line -> the exit code: check 0 OK / 1 not
    OK; run 0 done / 1 any other end (launch_failed included) / 2 refused
    or the job folder could not be made."""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="backslashreplace")
            except (ValueError, OSError):
                pass
    parser = argparse.ArgumentParser(prog="autona_plugin.py",
                                     description="Autona's fake host (Worker Protocol 1).")
    parser.add_argument("--version", action="version", version=f"autona_plugin {SDK_VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="check a plugin folder")
    check.add_argument("folder")
    check.add_argument("--dev", action="store_true", help="check it as a dev folder")
    check.add_argument("--source", choices=("manual",),
                       help="check it as installed by hand from a zip (taguyan. is refused)")
    check.add_argument("--host-version", help="report min_autona against this Autona version")
    check.add_argument("--json", action="store_true", help="print the report as JSON")
    run = sub.add_parser("run", help="run a job as Autona does")
    run.add_argument("folder")
    run.add_argument("job")
    run.add_argument("--params", help="a JSON file of the job's values")
    run.add_argument("--input", action="append", metavar="KEY=PATH")
    run.add_argument("--setting", action="append", metavar="KEY=VALUE")
    run.add_argument("--secret", action="append", metavar="KEY=VALUE",
                     help="a secret setting (your shell's history keeps it: use a test value)")
    run.add_argument("--trigger", metavar="action:<target>|hook:<event>")
    run.add_argument("--cancel-after", type=float, metavar="N")
    run.add_argument("--timeout", type=float, metavar="N")
    run.add_argument("--locale", default="ja", choices=("ja", "en"))
    run.add_argument("--host-version")
    run.add_argument("--work", help="the folder for job folders")
    run.add_argument("--json", action="store_true", help="print events and the end as JSON")
    args = parser.parse_args(argv)

    if args.command == "check":
        report = check_folder(args.folder, dev=args.dev, host_version=args.host_version,
                              source=args.source)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            _print_check(report)
        return 0 if report["ok"] else 1

    try:
        params = None
        if args.params:
            with open(args.params, encoding="utf-8-sig") as file:
                params = json.load(file)
            if not isinstance(params, dict):
                raise Refused("invalid_request", "--params: a JSON object")
        prepared = prepare_run(
            args.folder, args.job, params=params, inputs=_pairs(args.input, "--input"),
            settings=_pairs(args.setting, "--setting"), secrets=_pairs(args.secret, "--secret"),
            trigger=args.trigger, timeout=args.timeout, work=args.work, locale=args.locale,
            host_version=args.host_version)
    except Refused as refusal:
        if args.json:
            print(json.dumps({"refused": refusal.reason, "detail": refusal.detail}))
        else:
            print(f"refused: {refusal}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as problem:
        print(f"could not prepare the run: {problem}", file=sys.stderr)
        return 2

    def show(event):
        if args.json:
            print(json.dumps({key: value for key, value in event.items() if key != "raw"},
                             ensure_ascii=False), flush=True)
        else:
            print(_describe_event(event, args.locale), flush=True)

    if not args.json:
        print(f"run {prepared.request['plugin']}/{args.job} in {prepared.folder}")
    result = execute(prepared, cancel_after=args.cancel_after, on_event=show)
    if args.json:
        print(json.dumps({"end": result.as_dict()}, ensure_ascii=False))
    else:
        reason = f" ({result.reason})" if result.reason else ""
        print(f"end: {result.state}{reason}, exit code {result.exit_code}")
        for path, why in result.rejected:
            print(f"  artifact dropped: {path} ({why})")
        print(f"  worker.log: {prepared.log_path}")
    return 0 if result.state == "done" else 1


if __name__ == "__main__":
    sys.exit(cli())
