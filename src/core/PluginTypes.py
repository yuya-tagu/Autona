"""The plugin contract's types (ADR-009 W0): data, enums and the parts'
interfaces -- no logic.

ADR-009 lets third parties bring *jobs* (one process that makes outputs from
inputs and ends), started by hand, from an action on a page, or by a hook.
The parts that run them are built in parallel lanes against one contract:

- ``docs/plugins/contract_1.json`` -- the machine-readable source of truth
  (Worker Protocol 1, manifest 1, the input formats, the limits, the end
  table, the environment). ``tests/test_plugin_contract.py`` holds every
  enum and constant below to it;
- ``docs/plugins/plugin.schema.json`` -- the manifest's JSON Schema;
- ``docs/plugins/routes_v1.json`` -- the fixed v1 routes and their access
  classes;
- ``docs/plugins/inputs/`` -- the input sources' golden files.

A part's lane that finds the contract short reports it to the main session;
the main session changes the contract (these files and this module), not
the lane.

The docs folder is not shipped in the frozen app, so the values a running
host needs (patterns, limits, environment names) are here as constants too.

Naming: the dataclasses' fields are spelled like the contract's JSON keys
(``timeout_seconds``, ``run_id``) so a part maps them one to one; methods
are PascalCase and parameters camelCase, as the rest of core. A text
(文言) is a ``str`` or a locale -> str mapping (``Text``).

Nothing is made, read or written on import (pitfalls #1); no Qt
(tests/test_core_qt_free.py).
"""

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import (Any, Callable, ClassVar, Mapping, Optional, Protocol,
                    Sequence, Tuple, Union)

# A text (文言): a string, or locale -> string (at least one entry). Shown
# in the current locale, then en, then ja, then the first value.
Text = Union[str, Mapping[str, str]]

_EMPTY = MappingProxyType({})


# ── versions ────────────────────────────────────────────────────────────

PROTOCOL_VERSION = 1
MANIFEST_VERSION = 1
HOST_SPEAKS_PROTOCOLS = (1,)
HOST_READS_MANIFESTS = (1,)


# ── patterns (contract_1.json "patterns"; strings, compiled by the user) ─

PLUGIN_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,18}\.[a-z0-9][a-z0-9-]{0,19}$"
PLUGIN_ID_MAX_LENGTH = 40
RESERVED_ID_PREFIX = "taguyan."
JOB_ID_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"
FIELD_KEY_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"
HOOK_KEY_PATTERN = r"^[a-z][a-z0-9_]{0,31}/[a-z][a-z0-9_]{0,31}$"
# A field type, a hook's on, an action's target: an open name (an unknown
# one makes the job unusable, not the manifest).
NAME_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"
PLATFORM_PATTERN = r"^[a-z0-9][a-z0-9-]{0,31}$"
HOST_PLATFORM = "windows-x64"
VERSION_PATTERN = (r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
# The job folder's name: ArchiveJobService.JOB_ID_PATTERN's shape.
RUN_ID_PATTERN = r"^[0-9]{8}-[0-9]{6}-[a-z0-9]{4}$"
ERROR_CODE_PATTERN = r"^[a-z0-9_]{1,64}$"
MATCH_RECORD_ID_PATTERN = r"^[0-9]{8}-[0-9]{6}-[0-9]+-[0-9a-f]{4}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
FILE_EXTENSION_PATTERN = r"^[a-z0-9]{1,10}$"
# A dev folder's stable id: the first 12 hex digits of the SHA-256 of its
# normalised absolute path (os.path.normcase(os.path.abspath(path)), UTF-8).
DEV_FOLDER_ID_PATTERN = r"^[0-9a-f]{12}$"
# An entry's first word starting with this is reserved (a bundled runtime,
# "@python"): not accepted.
RESERVED_ENTRY_PREFIX = "@"
# What an invalid error.code becomes.
FALLBACK_ERROR_CODE = "error"
# The official plugin repositories' numeric GitHub ids (D7: a taguyan. id
# installs only from these; PluginRegistry.Check's id_reserved and
# PluginInstaller.DecideSource share it). Empty until the first official
# plugin repository exists.
OFFICIAL_REPO_IDS: frozenset = frozenset()


# ── limits (contract_1.json "limits") ───────────────────────────────────

SUMMARY_MAX_LENGTH = 500
MAX_WARNINGS = 200
MAX_ARTIFACTS = 50
LINE_MAX_BYTES = 1024 * 1024
WORKER_LOG_MAX_BYTES = 10 * 1024 * 1024
# The host's own display limits (not ADR-009's): longer text is truncated.
LOG_MESSAGE_MAX_LENGTH = 2000
ERROR_MESSAGE_MAX_LENGTH = 2000
ERROR_HINT_MAX_LENGTH = 1000
LABEL_MAX_LENGTH = 200
AUTHOR_MAX_LENGTH = 80
LIGHT_TIMEOUT_SECONDS = (1, 300)
HEAVY_TIMEOUT_SECONDS = (1, 43200)
HOOK_TIMEOUT_CAP_SECONDS = 30
LIGHT_MEMORY_MB = (64, 1024)
HOOK_JOB_MEMORY_MB_MAX = 512
DESCRIBE_TIMEOUT_SECONDS = 60
CANCEL_GRACE_SECONDS = 10
SHUTDOWN_GRACE_SECONDS = MappingProxyType({"desktop": 5, "headless": 2})
MANIFEST_MAX_BYTES = 256 * 1024
ICON_MAX_BYTES = 256 * 1024
NAME_MAX_LENGTH = 80
DESCRIPTION_MAX_LENGTH = 500
URL_MAX_LENGTH = 2048
SELECT_MAX_OPTIONS = 100
TEXT_MAX_LENGTH = MappingProxyType({"text_default": 200,
                                    "textarea_default": 4000,
                                    "cap": 20000})
SECRET_MAX_LENGTH = 4096
RUNS_KEPT_PER_PLUGIN = 20
HOOK_HISTORY_PER_HOOK = 20
HOOK_PENDING_PER_HOOK = 1
HOOK_PENDING_MAX_SECONDS = 600
HOOK_RATE_WINDOW_SECONDS = 60
HOOK_RATE_WINDOW_MAX_STARTS = 6
HOOK_HOUR_WINDOW_SECONDS = 3600
HOOK_HOUR_WINDOW_MAX_STARTS = 60
HOOK_CONSECUTIVE_FAILURES_DISABLE = 5
MATCH_DECIDED_HOLD_SECONDS = 5
RESULT_IMAGE_CHOICES = 30
TEXT_ARTIFACT_MAX_BYTES = 64 * 1024
IMAGE_ARTIFACT_MAX_BYTES = 20 * 1024 * 1024
IMAGE_ARTIFACT_EXTENSIONS = ("png", "jpg", "jpeg", "webp")
RELEASE_DESCRIPTOR_MAX_BYTES = 64 * 1024
ZIP_MAX_BYTES = 2 * 1024 ** 3
ZIP_EXPANDED_MAX_BYTES = 4 * 1024 ** 3
ZIP_MAX_ENTRIES = 50000
INSTALLED_PATH_MAX_LENGTH = 259
BLOCKLIST_REFRESH_SECONDS = 6 * 3600
UPDATE_CHECK_MIN_INTERVAL_SECONDS = 6 * 3600


# ── environment (contract_1.json "environment") ─────────────────────────

# Removed from the parent's environment, matched case-insensitively.
ENV_REMOVE_PREFIXES = ("AUTONA_",)
ENV_REMOVE_NAMES = ("QT_API",)
# Added after the removal.
ENV_PYTHONIOENCODING = "PYTHONIOENCODING"
ENV_PYINSTALLER_RESET = "PYINSTALLER_RESET_ENVIRONMENT"
ENV_PLUGIN_DIR = "AUTONA_PLUGIN_DIR"
ENV_PROTOCOL = "AUTONA_PROTOCOL"
# + the field key in upper case.
ENV_SECRET_PREFIX = "AUTONA_SECRET_"
# Only when permissions.autona_api is read or write.
ENV_API_URL = "AUTONA_API_URL"


# ── enums ───────────────────────────────────────────────────────────────

class Lane(StrEnum):
    LIGHT = "light"
    HEAVY = "heavy"


class FieldType(StrEnum):
    """A field's ``type`` this host knows: a value type (``params`` /
    ``settings``) or an input source type (``params`` only; the host places
    a file). A manifest may name another (a newer host's): that job is
    unusable (FIELD_TYPE_UNKNOWN), the manifest is not."""
    TEXT = "text"
    TEXTAREA = "textarea"
    NUMBER = "number"
    BOOL = "bool"
    SELECT = "select"
    FILE = "file"
    FOLDER = "folder"
    SECRET = "secret"
    MATCH_LOG = "match_log"
    MATCH_RECORD = "match_record"
    STATE = "state"
    RESULT_IMAGE = "result_image"
    THUMBNAIL_IMAGE = "thumbnail_image"
    ARCHIVE_EXPORT = "archive_export"


VALUE_FIELD_TYPES = frozenset({
    FieldType.TEXT, FieldType.TEXTAREA, FieldType.NUMBER, FieldType.BOOL,
    FieldType.SELECT, FieldType.FILE, FieldType.FOLDER, FieldType.SECRET})
SOURCE_FIELD_TYPES = frozenset({
    FieldType.MATCH_LOG, FieldType.MATCH_RECORD, FieldType.STATE,
    FieldType.RESULT_IMAGE, FieldType.THUMBNAIL_IMAGE,
    FieldType.ARCHIVE_EXPORT})
# Only a trigger fills these: a job holding one has manual false.
CONTEXT_ONLY_TYPES = frozenset({FieldType.MATCH_RECORD,
                                FieldType.ARCHIVE_EXPORT})
# What a manual run fills (STATE has no choice: the state at the press).
MANUAL_PICK_TYPES = frozenset({
    FieldType.MATCH_LOG, FieldType.STATE, FieldType.RESULT_IMAGE,
    FieldType.THUMBNAIL_IMAGE})
# Desktop features: not available headless.
DESKTOP_ONLY_TYPES = frozenset({FieldType.RESULT_IMAGE,
                                FieldType.THUMBNAIL_IMAGE})
# What an action / a hook fills besides its own type (the snapshot at the
# trigger): contract_1.json field_types.source[].at_trigger.
AT_TRIGGER_TYPES = frozenset({FieldType.MATCH_LOG, FieldType.STATE})
# Input formats, per source type.
INPUT_FORMATS = MappingProxyType({
    FieldType.MATCH_LOG: 1,
    FieldType.MATCH_RECORD: 1,
    FieldType.STATE: 1,
    FieldType.RESULT_IMAGE: 1,
    FieldType.THUMBNAIL_IMAGE: 1,
    FieldType.ARCHIVE_EXPORT: 1,
})


class FilesScope(StrEnum):
    """permissions.files (declared, not enforced)."""
    INPUTS = "inputs"
    DATA = "data"
    USER = "user"
    ANYWHERE = "anywhere"


class AutonaApiUse(StrEnum):
    """permissions.autona_api (declared, not enforced)."""
    NONE = "none"
    READ = "read"
    WRITE = "write"


class TriggerKind(StrEnum):
    MANUAL = "manual"
    ACTION = "action"
    HOOK = "hook"


class ActionTarget(StrEnum):
    RESULT_IMAGE = "result_image"
    THUMBNAIL_IMAGE = "thumbnail_image"
    ARCHIVE_EXPORT = "archive_export"


class HookEvent(StrEnum):
    MATCH_DECIDED = "match_decided"
    RESULT_IMAGE_SAVED = "result_image_saved"


# The type of field each trigger fills (ADR-009 B.4).
ACTION_FILLS = MappingProxyType({
    ActionTarget.RESULT_IMAGE: FieldType.RESULT_IMAGE,
    ActionTarget.THUMBNAIL_IMAGE: FieldType.THUMBNAIL_IMAGE,
    ActionTarget.ARCHIVE_EXPORT: FieldType.ARCHIVE_EXPORT,
})
HOOK_FILLS = MappingProxyType({
    HookEvent.MATCH_DECIDED: FieldType.MATCH_RECORD,
    HookEvent.RESULT_IMAGE_SAVED: FieldType.RESULT_IMAGE,
})


class EventType(StrEnum):
    """A stdout event's ``type`` (Worker Protocol 1)."""
    STAGE = "stage"
    PROGRESS = "progress"
    LOG = "log"
    WARNING = "warning"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


TERMINAL_EVENTS = frozenset({EventType.DONE, EventType.ERROR,
                             EventType.CANCELLED})


class ArtifactKind(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    JSON = "json"


class ArtifactRejection(StrEnum):
    """Why an artifact is dropped (a host warning is added to the run)."""
    OUTSIDE_OUT_DIR = "outside_out_dir"
    SYMLINK_OR_JUNCTION = "symlink_or_junction"
    HARD_LINK = "hard_link"
    MISSING = "missing"
    NOT_A_FILE = "not_a_file"
    TOO_LARGE = "too_large"
    BAD_EXTENSION = "bad_extension"
    UNKNOWN_KIND = "unknown_kind"


class JobState(StrEnum):
    """A run's state (job.json ``state``). Every state but RUNNING is an
    end."""
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EndReason(StrEnum):
    """Why a run ended FAILED beyond its terminal line (job.json
    ``reason``)."""
    LAUNCH_FAILED = "launch_failed"
    ENTRY_MISSING = "entry_missing"
    TIMED_OUT = "timed_out"
    MEMORY_LIMIT = "memory_limit"
    ABNORMAL_EXIT = "abnormal_exit"
    AUTONA_EXITED = "autona_exited"


class DescribeFailure(StrEnum):
    """Why ``describe`` made a plugin unusable. Judged by PluginRegistry
    from PluginRunner.Describe's facts (DescribeRun)."""
    NO_LINE = "no_line"
    NOT_JSON = "not_json"
    TYPE_NOT_DESCRIBE = "type_not_describe"
    PROTOCOL_MISSING = "protocol_missing"
    NO_COMMON_PROTOCOL = "no_common_protocol"
    ID_MISMATCH = "id_mismatch"
    VERSION_MISMATCH = "version_mismatch"
    NONZERO_EXIT = "nonzero_exit"
    TIMED_OUT = "timed_out"
    LAUNCH_FAILED = "launch_failed"


class PluginState(StrEnum):
    """The list's ``state`` (GET /api/v1/plugins): the plugin's, never a
    hook's (an auto-disabled hook shows on its hook row only). When more
    than one applies the first of STATE_PRECEDENCE is shown."""
    READY = "ready"
    DISABLED = "disabled"
    BLOCKED = "blocked"
    NEEDS_SETTINGS = "needs_settings"
    INCOMPATIBLE = "incompatible"
    RUNNING = "running"


STATE_PRECEDENCE = (PluginState.BLOCKED, PluginState.INCOMPATIBLE,
                    PluginState.DISABLED, PluginState.RUNNING,
                    PluginState.NEEDS_SETTINGS, PluginState.READY)


class Badge(StrEnum):
    """The list's ``badge``: how the plugin came."""
    LISTED = "listed"
    MANUAL = "manual"
    DEV = "dev"
    BUILTIN = "builtin"


class StartRefusal(StrEnum):
    """Why a request is refused (contract_1.json refusals: the v1 status of
    each is there)."""
    BUSY = "busy"
    NEEDS_SETTINGS = "needs_settings"
    BLOCKED = "blocked"
    INCOMPATIBLE = "incompatible"
    DISABLED = "disabled"
    JOB_UNUSABLE = "job_unusable"
    ID_TAKEN = "id_taken"
    INVALID_REQUEST = "invalid_request"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"


class ProblemScope(StrEnum):
    PLUGIN = "plugin"
    JOB = "job"
    WARNING = "warning"


class ManifestCheck(StrEnum):
    """A manifest problem's code (contract_1.json manifest_checks: the scope
    and the owner of each are there)."""
    MANIFEST_UNREADABLE = "manifest_unreadable"
    MANIFEST_VERSION_UNKNOWN = "manifest_version_unknown"
    INVALID_FIELD = "invalid_field"
    INVALID_JOB_FIELD = "invalid_job_field"
    UNKNOWN_KEY = "unknown_key"
    ID_FOLDER_MISMATCH = "id_folder_mismatch"
    ID_RESERVED = "id_reserved"
    ID_TAKEN = "id_taken"
    PROTOCOL_UNSUPPORTED = "protocol_unsupported"
    PLATFORM_UNSUPPORTED = "platform_unsupported"
    MIN_AUTONA_NEWER = "min_autona_newer"
    ENTRY_RESERVED = "entry_reserved"
    ENTRY_OUTSIDE = "entry_outside"
    ENTRY_MISSING = "entry_missing"
    ICON_INVALID = "icon_invalid"
    KEY_DUPLICATE = "key_duplicate"
    SECRET_OUTSIDE_SETTINGS = "secret_outside_settings"
    SOURCE_IN_SETTINGS = "source_in_settings"
    FIELD_TYPE_UNKNOWN = "field_type_unknown"
    HOOK_EVENT_UNKNOWN = "hook_event_unknown"
    ACTION_TARGET_UNKNOWN = "action_target_unknown"
    HOOK_DUPLICATE = "hook_duplicate"
    TRIGGER_NEEDS_LIGHT_LANE = "trigger_needs_light_lane"
    FILL_COUNT = "fill_count"
    TRIGGER_UNFILLABLE = "trigger_unfillable"
    MANUAL_WITH_CONTEXT = "manual_with_context"
    HOOK_PARAM_NEEDS_DEFAULT = "hook_param_needs_default"
    DEFAULT_INVALID = "default_invalid"
    TIMEOUT_OUT_OF_RANGE = "timeout_out_of_range"
    MEMORY_OUT_OF_RANGE = "memory_out_of_range"
    JOB_UNREACHABLE = "job_unreachable"


class HookOutcome(StrEnum):
    """A hook history entry's outcome."""
    DONE = "done"
    ERROR = "error"
    FAILED = "failed"
    CANCELLED = "cancelled"
    OVERWRITTEN = "overwritten"
    DISCARDED = "discarded"
    SKIPPED_RATE_LIMIT = "skipped_rate_limit"
    NEEDS_SETTINGS = "needs_settings"


class HookDisabledReason(StrEnum):
    """Why a hook is off (HookStatus.disabled_reason; contract_1.json
    hooks.disabled_reasons_note). USER is the user's; RATE_LIMIT, FAILURES
    and PARAMS_CHANGED the dispatcher's own, per hook; the rest a reason
    PluginHooks.Forget is given, for every hook of the plugin."""
    USER = "user"
    RATE_LIMIT = "rate_limit"
    FAILURES = "failures"
    RECONSENT = "reconsent"
    NEW_INSTALL = "new_install"
    BLOCKED = "blocked"
    REMOVED = "removed"
    PARAMS_CHANGED = "params_changed"


class InstallDecision(StrEnum):
    """What installing over an installed id is (ADR-009 C.3)."""
    UPDATE = "update"
    CONFIRM_SAME_OR_OLDER = "confirm_same_or_older"
    NEW_INSTALL = "new_install"
    REFUSE_DEV_ID = "refuse_dev_id"
    REFUSE_RESERVED = "refuse_reserved"


class InstallSourceKind(StrEnum):
    """install.json ``source.kind``: what an installed plugin came from
    (C.3). A dev folder and the built-in have no install.json."""
    GITHUB = "github"
    MANUAL = "manual"


class ZipProblem(StrEnum):
    """PluginInstaller.CheckZip's codes (C.1 / C.7; contract_1.json
    install.zip_problems): the zip's own rules first, then the plugin's
    layout and the release descriptor."""
    ZIP_UNREADABLE = "zip_unreadable"
    TOO_MANY_ENTRIES = "too_many_entries"
    EXPANDED_TOO_LARGE = "expanded_too_large"
    UNSAFE_PATH = "unsafe_path"
    BAD_NAME = "bad_name"
    NOT_ASCII = "not_ascii"
    RESERVED_NAME = "reserved_name"
    LINK = "link"
    ENCRYPTED = "encrypted"
    DUPLICATE_NAME = "duplicate_name"
    PATH_TOO_LONG = "path_too_long"
    ZIP_TOO_LARGE = "zip_too_large"
    DESCRIPTOR_INVALID = "descriptor_invalid"
    SIZE_MISMATCH = "size_mismatch"
    SHA256_MISMATCH = "sha256_mismatch"
    NOT_ONE_FOLDER = "not_one_folder"
    FOLDER_NOT_ID = "folder_not_id"
    MANIFEST_MISSING = "manifest_missing"
    MANIFEST_UNREADABLE = "manifest_unreadable"
    ID_MISMATCH = "id_mismatch"
    VERSION_MISMATCH = "version_mismatch"
    ENTRY_OUTSIDE = "entry_outside"
    ENTRY_MISSING = "entry_missing"


# ── the manifest (plugin.json) ──────────────────────────────────────────

@dataclass(frozen=True)
class Option:
    value: str
    label: Text


@dataclass(frozen=True)
class Field:
    """One item of ``params`` / ``settings``. ``type`` is a FieldType value,
    or the manifest's string when it is not one (the job is then unusable,
    with the reason). The extras are None / empty unless the type has them."""
    key: str
    type: str
    label: Text
    required: bool = False
    default: Any = None
    help: Optional[Text] = None
    max_length: Optional[int] = None
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    options: Tuple[Option, ...] = ()
    extensions: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Permissions:
    """What the author declares (not enforced)."""
    network: Tuple[str, ...]
    files: str
    spawn: bool
    autona_api: str
    note: Optional[Text] = None


@dataclass(frozen=True)
class Action:
    """``target`` is an ActionTarget value or an unknown name."""
    target: str
    label: Text


@dataclass(frozen=True)
class Hook:
    """``on`` is a HookEvent value or an unknown name. Its key is
    ``<job id>/<on>``."""
    on: str
    label: Text


@dataclass(frozen=True)
class Job:
    """``timeout_seconds`` None is no time limit: a built-in job's only
    (vodsplit); a manifest's job always has one (the schema)."""
    id: str
    title: Text
    lane: str
    timeout_seconds: Optional[int]
    memory_mb: Optional[int] = None
    params: Tuple[Field, ...] = ()
    manual: bool = True
    actions: Tuple[Action, ...] = ()
    hooks: Tuple[Hook, ...] = ()


@dataclass(frozen=True)
class UpdateSource:
    github: str


@dataclass(frozen=True)
class Manifest:
    manifest: int
    id: str
    version: str
    name: Text
    author: str
    license: str
    min_autona: str
    protocol: Tuple[int, ...]
    platforms: Tuple[str, ...]
    entry: Tuple[str, ...]
    permissions: Permissions
    jobs: Tuple[Job, ...]
    description: Optional[Text] = None
    homepage: Optional[str] = None
    funding: Optional[str] = None
    icon: Optional[str] = None
    update: Optional[UpdateSource] = None
    settings: Tuple[Field, ...] = ()


@dataclass(frozen=True)
class ManifestProblem:
    """One finding of a manifest check. ``path`` points at the item
    (``jobs[0].params[1].type``); ``job`` names the job for a JOB scope."""
    code: ManifestCheck
    scope: ProblemScope
    path: str = ""
    job: Optional[str] = None
    detail: str = ""


@dataclass(frozen=True)
class ManifestReport:
    """A checked manifest: None when it could not be read as one (a PLUGIN
    problem says why). The plugin is usable when no problem has scope
    PLUGIN; a job is usable when no JOB problem names it."""
    manifest: Optional[Manifest]
    problems: Tuple[ManifestProblem, ...] = ()


# ── describe ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DescribeRun:
    """The facts of one ``<entry> describe`` (PluginRunner.Describe): no
    judgement. ``objects`` are the stdout lines that were JSON objects, in
    order (the first ones; ``stdout_lines`` counts every non-empty line)."""
    launched: bool
    entry_missing: bool = False
    timed_out: bool = False
    exit_code: Optional[int] = None
    stdout_lines: int = 0
    objects: Tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class DescribeAnswer:
    """``{"type": "describe", "protocol": [...], "id", "version"}``."""
    protocol: Tuple[int, ...]
    id: str
    version: str
    type: ClassVar[str] = "describe"


@dataclass(frozen=True)
class DescribeResult:
    """PluginRegistry's judgement of a DescribeRun against the manifest:
    the protocol to use when usable, else why not."""
    answer: Optional[DescribeAnswer]
    protocol: Optional[int] = None
    failure: Optional[DescribeFailure] = None
    detail: str = ""


# ── a run: request.json ─────────────────────────────────────────────────

@dataclass(frozen=True)
class Trigger:
    """request.json ``trigger``: ``target`` with ACTION, ``event`` and
    ``hook`` (the hook key) with HOOK."""
    kind: TriggerKind
    target: Optional[ActionTarget] = None
    event: Optional[HookEvent] = None
    hook: Optional[str] = None


@dataclass(frozen=True)
class InputFile:
    """request.json ``inputs[key]``: a file the host placed in the job
    folder's ``inputs/`` (named ``<key><ext>``)."""
    kind: FieldType
    format: int
    path: str
    sha256: Optional[str] = None


@dataclass(frozen=True)
class HostInfo:
    version: str
    name: str = "Autona"


@dataclass(frozen=True)
class RunRequest:
    """request.json (UTF-8, no BOM). ``params`` / ``settings`` hold every
    key (an empty optional value is its default, else None; a file / folder
    is the absolute path the host resolved); ``inputs`` every source key
    (None when an optional one is not filled). Never a secret."""
    protocol: int
    plugin: str
    job: str
    run_id: str
    trigger: Trigger
    locale: str
    params: Mapping[str, Any]
    settings: Mapping[str, Any]
    inputs: Mapping[str, Optional[InputFile]]
    out_dir: str
    data_dir: str
    host: HostInfo


@dataclass(frozen=True)
class InputPick:
    """What fills one source field of a start: a choice id from
    ``PluginInputs.Choices`` (manual), or the trigger's context -- the image
    file (an action / result_image_saved) or the match record's line
    (match_decided). A field with no pick is filled at the trigger
    (AT_TRIGGER_TYPES; STATE always, manual included) or left None."""
    key: str
    kind: FieldType
    choice: Optional[str] = None
    path: Optional[str] = None
    record: Optional[Mapping[str, Any]] = None


# ── a run: the events (stdout) ──────────────────────────────────────────
#
# Every event keeps ``raw``: the line's whole object as read (unknown keys
# included). A built-in worker's own terminal fields -- vodsplit's done
# (sets / needs_review / clips / skipped / template ...) -- are read from
# it (ADR-009 D9, I2). Not compared: two events are equal by their typed
# fields.

@dataclass(frozen=True)
class StageEvent:
    stage: str
    label: Optional[Text] = None
    index: Optional[int] = None
    count: Optional[int] = None
    raw: Mapping[str, Any] = field(default=_EMPTY, compare=False, repr=False)
    type: ClassVar[EventType] = EventType.STAGE


@dataclass(frozen=True)
class ProgressEvent:
    stage: Optional[str] = None
    fraction: Optional[float] = None
    overall: Optional[float] = None
    eta: Optional[float] = None
    raw: Mapping[str, Any] = field(default=_EMPTY, compare=False, repr=False)
    type: ClassVar[EventType] = EventType.PROGRESS


@dataclass(frozen=True)
class LogEvent:
    message: Text
    raw: Mapping[str, Any] = field(default=_EMPTY, compare=False, repr=False)
    type: ClassVar[EventType] = EventType.LOG


@dataclass(frozen=True)
class WarningEvent:
    message: Text
    raw: Mapping[str, Any] = field(default=_EMPTY, compare=False, repr=False)
    type: ClassVar[EventType] = EventType.WARNING


@dataclass(frozen=True)
class Artifact:
    """``done.artifacts[]``: ``path`` relative to out_dir."""
    path: str
    kind: ArtifactKind
    label: Optional[Text] = None


@dataclass(frozen=True)
class DoneEvent:
    summary: Optional[Text] = None
    artifacts: Tuple[Artifact, ...] = ()
    raw: Mapping[str, Any] = field(default=_EMPTY, compare=False, repr=False)
    type: ClassVar[EventType] = EventType.DONE


@dataclass(frozen=True)
class ErrorEvent:
    code: str
    message: Text
    hint: Optional[Text] = None
    raw: Mapping[str, Any] = field(default=_EMPTY, compare=False, repr=False)
    type: ClassVar[EventType] = EventType.ERROR


@dataclass(frozen=True)
class CancelledEvent:
    raw: Mapping[str, Any] = field(default=_EMPTY, compare=False, repr=False)
    type: ClassVar[EventType] = EventType.CANCELLED


WorkerEvent = Union[StageEvent, ProgressEvent, LogEvent, WarningEvent,
                    DoneEvent, ErrorEvent, CancelledEvent]
TerminalEvent = Union[DoneEvent, ErrorEvent, CancelledEvent]
# The field every event has besides the contract's (not a protocol key).
RAW_FIELD = "raw"


# ── a run: the process and its end ──────────────────────────────────────

@dataclass(frozen=True)
class LaunchSpec:
    """What PluginRunner starts: ``argv`` is the entry plus ``run
    <request.json>`` (a built-in worker's own arguments for vodsplit);
    ``env`` is BuildEnvironment's; ``timeout_seconds`` None is no limit
    (vodsplit); ``memory_mb`` is enforced on the light lane only.
    ``log_path`` is the run's worker.log. ``env`` holds the secrets: it is
    left out of the repr."""
    argv: Tuple[str, ...]
    cwd: str
    env: Mapping[str, str] = field(repr=False)
    lane: Lane
    log_path: str
    timeout_seconds: Optional[int] = None
    memory_mb: Optional[int] = None


@dataclass(frozen=True)
class RunOutcome:
    """How a process ended (contract_1.json "end"). ``final_raw`` is the
    last terminal line's whole object (``final.raw``), kept for a built-in
    worker's own fields."""
    state: JobState
    reason: Optional[EndReason] = None
    exit_code: Optional[int] = None
    final: Optional[TerminalEvent] = None
    final_raw: Optional[Mapping[str, Any]] = None
    cancel_requested: bool = False
    timed_out: bool = False
    finished_before_cancel: bool = False
    peak_memory_bytes: Optional[int] = None


@dataclass(frozen=True)
class RunRecord:
    """A run as kept in job.json and answered by GET .../runs/<run_id>
    (contract_1.json job_record). No secret, no environment."""
    run_id: str
    plugin: str
    job: str
    trigger: Trigger
    lane: Lane
    state: JobState
    started_at: str
    reason: Optional[EndReason] = None
    finished_at: Optional[str] = None
    exit_code: Optional[int] = None
    stage: Optional[StageEvent] = None
    progress: Optional[ProgressEvent] = None
    log: Optional[Text] = None
    warnings: Tuple[Text, ...] = ()
    summary: Optional[Text] = None
    artifacts: Tuple[Artifact, ...] = ()
    error: Optional[ErrorEvent] = None
    cancel_requested: bool = False
    finished_before_cancel: bool = False
    timeout_seconds: Optional[int] = None
    peak_memory_bytes: Optional[int] = None


@dataclass(frozen=True)
class Refused:
    """A request not carried out. ``remaining_seconds`` with BUSY when
    known."""
    reason: StartRefusal
    remaining_seconds: Optional[float] = None
    detail: str = ""


# ── the registry and the list ───────────────────────────────────────────

@dataclass(frozen=True)
class ListEntry:
    """One row of GET /api/v1/plugins (THIS_PC): these fields and no
    others -- no reason text, error, path or history."""
    id: str
    name: Text
    author: str
    version: str
    state: PluginState
    badge: Badge
    size_bytes: int
    has_icon: bool


@dataclass(frozen=True)
class RegisteredPlugin:
    """A plugin the registry knows: its folder, how it came, its checked
    manifest and its state. ``id`` is the folder's name when the manifest
    could not be read."""
    id: str
    folder: str
    badge: Badge
    report: ManifestReport
    state: PluginState
    size_bytes: int = 0
    has_icon: bool = False
    enabled: bool = True
    blocked_reason: Optional[Text] = None
    describe: Optional[DescribeResult] = None


@dataclass(frozen=True)
class DevFolder:
    """A dev folder (user_data/plugins/dev.json, or AUTONA_PLUGIN_DEV when
    ``from_env``). ``id`` is stable (DEV_FOLDER_ID_PATTERN: from its path),
    so a removal names the folder, not a position."""
    id: str
    path: str
    plugin_id: Optional[str] = None
    from_env: bool = False


@dataclass(frozen=True)
class InputChoice:
    """A manual choice of a source type (GET /api/v1/plugins/inputs/<kind>):
    ``id`` is what the start's InputPick.choice names; the page never sends
    a path."""
    id: str
    label: str
    detail: str = ""


# ── hooks ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class HookHistoryEntry:
    at: str
    outcome: HookOutcome
    summary: Optional[Text] = None
    reason: Optional[str] = None
    run_id: Optional[str] = None


@dataclass(frozen=True)
class HookStatus:
    """A hook's row (SHELL): its auto-disable shows here, not in the
    list's state."""
    plugin: str
    key: str
    enabled: bool
    params: Mapping[str, Any]
    pending: bool = False
    failures: int = 0
    disabled_reason: Optional[HookDisabledReason] = None
    last_error: Optional[ErrorEvent] = None
    history: Tuple[HookHistoryEntry, ...] = ()


# ── installs ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class InstallSource:
    """Where an install came from (install.json ``source``): ``kind`` an
    InstallSourceKind value -- ``github`` with the numeric ``repo_id`` and
    the ``owner/repo`` name it had (display only), or ``manual`` (neither)."""
    kind: str
    repo_id: Optional[int] = None
    name: Optional[str] = None


@dataclass(frozen=True)
class InstallRecord:
    """user_data/plugins/<id>/install.json (contract_1.json
    install.record): ``sha256`` the installed zip's, ``installed_at`` ISO
    8601 with offset (seconds), written by the installer."""
    source: InstallSource
    version: str
    sha256: Optional[str]
    consent_digest: str
    installed_at: Optional[str] = None


@dataclass(frozen=True)
class BlockEntry:
    """A row of plugins.json ``blocked``: by ``id`` (+ ``versions``: ``*``
    or one comparison and X.Y.Z), by the installed zip's ``sha256``, or by
    ``repo_id``."""
    reason: Text
    since: str
    id: Optional[str] = None
    versions: Optional[str] = None
    sha256: Optional[str] = None
    repo_id: Optional[int] = None


# ── the parts' interfaces ───────────────────────────────────────────────

class RunHandle(Protocol):
    """A started process (PluginRunner.Start)."""

    pid: Optional[int]

    def Cancel(self) -> None:
        """Ask the run to stop: the job's priority back to NORMAL, write
        ``cancel\\n``, close stdin; after CANCEL_GRACE_SECONDS kill the job
        object. Idempotent."""

    def Kill(self) -> None:
        """Kill this run's job object now (the run and every descendant in
        it; no other run): the shutdown's end after its grace (E.2), when a
        caller must not wait for CANCEL_GRACE_SECONDS. The end is decided
        and onEnd called as usual (a cancel requested before counts).
        Idempotent; nothing when it has ended."""

    def Wait(self, timeout: Optional[float] = None) -> Optional[RunOutcome]:
        """The outcome once ended, None if still running after
        ``timeout``."""


class PluginRunner(Protocol):
    """Runs one process (ADR-009 D6, appendix A/E; lane L1).

    Owns: the environment's subtraction, CREATE_SUSPENDED -> the job object
    (KILL_ON_JOB_CLOSE, BELOW_NORMAL priority, the light lane's memory) ->
    NtResumeProcess; the stdout / stderr readers (events with their raw,
    worker.log and its limits); the time limit and the timed_out mark; the
    cancel; the end decision; the peak memory. Knows nothing of manifests,
    lanes' occupancy or job folders (PluginJobs does), and judges no
    describe answer (PluginRegistry does)."""

    def BuildEnvironment(self, parent: Mapping[str, str], *, pluginDir: str,
                         protocol: Optional[int],
                         secrets: Mapping[str, str],
                         apiUrl: Optional[str]) -> dict:
        """``parent`` minus ENV_REMOVE_PREFIXES / ENV_REMOVE_NAMES
        (case-insensitive), plus PYTHONIOENCODING, PYINSTALLER_RESET_ENVIRONMENT,
        AUTONA_PLUGIN_DIR, and -- for a run, ``protocol`` not None --
        AUTONA_PROTOCOL, ``secrets`` (already AUTONA_SECRET_<KEY> names)
        and AUTONA_API_URL when ``apiUrl``. Pure."""

    def Describe(self, pluginDir: str, entry: Sequence[str], *,
                 dev: bool) -> DescribeRun:
        """Run ``<entry> describe`` in ``pluginDir`` (stdin DEVNULL, NORMAL
        priority, DESCRIBE_TIMEOUT_SECONDS, the describe environment) and
        say what happened: whether it launched, timed out, its exit code and
        the JSON objects it printed. No id / version / protocol check.
        Blocks the caller (a TaskExecutor job)."""

    def Start(self, spec: LaunchSpec,
              onEvent: Callable[[WorkerEvent], None],
              onEnd: Callable[[RunOutcome], None]) -> RunHandle:
        """Start ``spec``. ``onEvent`` gets each event (typed, with its
        raw; on a reader thread; it must not block); ``onEnd`` the outcome
        once, after the readers finished. A launch failure is an outcome
        (FAILED, LAUNCH_FAILED / ENTRY_MISSING), not an exception."""

    def EndState(self, final: Optional[EventType], exitCode: Optional[int],
                 *, cancelRequested: bool, timedOut: bool,
                 launchFailed: bool, entryMissing: bool,
                 memoryLimitHit: bool) -> Tuple[JobState,
                                                Optional[EndReason]]:
        """contract_1.json "end": launch failure, then timed_out, then the
        table. Pure."""

    def Shutdown(self, grace: float) -> None:
        """Cancel every run, wait up to ``grace`` seconds, kill the rest."""


class PluginRegistry(Protocol):
    """The plugins Autona knows (lane L2): the installed folders
    (``plugins/<id>/``), the built-in vodsplit, the dev folders. Reads and
    checks manifests (the schema -- the top level and ``settings`` as the
    plugin's, each job as its own -- contract_1.json manifest_checks, B.4),
    judges describe answers; starts nothing at launch."""

    def Scan(self) -> None:
        """Read every manifest again (launch, after an install / removal,
        the dev list's change). Owns ID_TAKEN: a dev folder and an install
        with one id (the later one is refused)."""

    def Check(self, raw: Any, folder: str, source: Optional[InstallSource],
              *, dev: bool, hostVersion: str,
              expectedId: Optional[str] = None) -> ManifestReport:
        """Check a parsed plugin.json (``raw``: what json.loads gave) in
        ``folder`` (absolute: ``entry`` / ``icon`` inside it and existing)
        coming from ``source`` (the taguyan. reservation against
        OFFICIAL_REPO_IDS; None for the built-in). ID_FOLDER_MISMATCH: the
        id against ``expectedId`` -- the installer's, for a ``<id>.new``
        folder -- else the folder's name; never for a dev folder. Touches no
        file but those."""

    def JudgeDescribe(self, run: DescribeRun,
                      manifest: Manifest) -> DescribeResult:
        """The DescribeFailure of ``run`` against ``manifest`` (id, version,
        a protocol in common with HOST_SPEAKS_PROTOCOLS), or the protocol to
        use. Pure."""

    def Entries(self) -> Sequence[ListEntry]:
        """The list's rows, in display order."""

    def Get(self, pluginId: str) -> Optional[RegisteredPlugin]:
        """One plugin, or None."""

    def Actions(self, target: ActionTarget) -> Sequence[Tuple[str, Job]]:
        """(plugin id, job) of every usable job with an action on
        ``target``."""

    def HookJobs(self, event: HookEvent) -> Sequence[Tuple[str, Job]]:
        """(plugin id, job) of every usable job with a hook on ``event``."""

    def DevFolders(self) -> Sequence[DevFolder]:
        """The dev folders (dev.json's -- also while the toggle is off --
        then AUTONA_PLUGIN_DEV's)."""

    def DevFolderActive(self, folderId: str) -> bool:
        """Whether the dev folder is scanned (dev.json's while the toggle
        is on; AUTONA_PLUGIN_DEV's always)."""

    def RefusedFolders(self) -> Sequence[Tuple[str, Tuple[ManifestProblem,
                                                         ...]]]:
        """(folder, problems) of each folder whose id another plugin owns
        (ID_TAKEN). Not list rows (a row's id names one plugin): the page
        shows them beside the list."""

    def SetDevMode(self, on: bool) -> Optional[Refused]:
        """The developer toggle (dev.json); UNAVAILABLE when dev.json
        cannot be written."""

    def AddDevFolder(self, path: str) -> Union[DevFolder, Refused]:
        """Add a dev folder: ID_TAKEN when another plugin has its id,
        INVALID_REQUEST when it is not a folder or is an installed one,
        UNAVAILABLE when dev.json cannot be written."""

    def RemoveDevFolder(self, folderId: str) -> Union[bool, Refused]:
        """Remove dev.json's folder ``folderId`` (the folder stays): False
        when it is not dev.json's, UNAVAILABLE when dev.json cannot be
        written."""

    def Reload(self, pluginId: str) -> Optional[DescribeResult]:
        """A dev plugin's reload: its manifest again, then describe."""


class PluginSettings(Protocol):
    """Non-secret settings (lane L3, with PluginSecrets):
    ``user_data/plugins/<id>/config.json`` (``_dev/<id>/`` for a dev one).
    A file / folder value arrives as a pick id (pickFile / pickFolder), is
    resolved to the absolute path by the host and stored so; it is shown by
    its file name only."""

    def Get(self, pluginId: str, manifest: Manifest, *,
            dev: bool) -> Mapping[str, Any]:
        """Every non-secret setting key -> its saved value, its default
        when not saved, else None (request.json ``settings``)."""

    def Save(self, pluginId: str, manifest: Manifest,
             values: Mapping[str, Any], *,
             dev: bool) -> Union[Mapping[str, Any], Refused]:
        """Check ``values`` against the manifest's settings (type, options,
        lengths; pick ids resolved) and save them; INVALID_REQUEST when one
        does not fit. Returns what Get returns afterwards."""

    def Missing(self, pluginId: str, manifest: Manifest, *,
                dev: bool) -> Tuple[str, ...]:
        """The keys of required settings that are not set -- config.json's
        and the secrets' (PluginSecrets.Keys) together. Non-empty is
        NEEDS_SETTINGS (no start, not a hook failure)."""


class PluginSecrets(Protocol):
    """``secret`` settings (lane L3): DPAPI (CurrentUser) in
    ``secrets.bin`` per plugin (``_dev/<id>/`` for a dev one). Never in a
    log, request.json or job.json; the plugin cannot write back."""

    def Set(self, pluginId: str, key: str, value: str, *,
            dev: bool) -> None:
        """Save one secret: 1 to SECRET_MAX_LENGTH characters, no line
        break, NUL or other control character (Unicode category Cc)."""

    def Delete(self, pluginId: str, key: str, *, dev: bool) -> None:
        """Clear one secret."""

    def Keys(self, pluginId: str, *, dev: bool) -> frozenset:
        """The keys that are set (for 'set' / needs_settings)."""

    def Environment(self, pluginId: str, *, dev: bool) -> dict:
        """AUTONA_SECRET_<KEY> -> value, for BuildEnvironment."""

    def Forget(self, pluginId: str, *, dev: bool) -> None:
        """Remove secrets.bin (removal, new install)."""


class PluginInputs(Protocol):
    """The input sources (lane L3): their manual choices and the files the
    host places in a run's ``inputs/`` (contract_1.json field_types.source;
    the golden files of docs/plugins/inputs/)."""

    def Choices(self, kind: FieldType) -> Sequence[InputChoice]:
        """The manual choices of a MANUAL_PICK_TYPES type (STATE has none:
        it is always the state at the press)."""

    def Prepare(self, key: str, kind: FieldType, inputsDir: str,
                pick: Optional[InputPick]) -> Optional[InputFile]:
        """Write ``<key><ext>`` into ``inputsDir`` from ``pick`` (or, with
        none, the snapshot at the trigger for AT_TRIGGER_TYPES) and say
        what it wrote; None when nothing fills it. Images carry their
        sha256; a .jpeg is written as .jpg. MATCH_LOG at the trigger: the
        log file of the open record's start date (today's when none), after
        MatchLogService.Flush()."""


class PluginJobs(Protocol):
    """Generic runs (lane L8): the two lanes and busy, the job folder
    (request.json, job.json, worker.log, inputs/, out/), the artifacts'
    checks, keeping the newest RUNS_KEPT_PER_PLUGIN manual / action runs,
    the launch's sweep (running -> failed / autona_exited).

    Owns both lanes: another owner (the built-in vodsplit, run by
    ArchiveJobService, on the heavy one) takes one with Claim and gives it
    back with Release.

    Made with two callbacks (constructor arguments, called outside its
    lock, on the thread that ended the run or released the claim):
    ``onRunEnded(record: RunRecord)`` for every run's end
    (PluginHooks.RunEnded for a hook's) and ``onLaneFreed(lane: Lane)``
    when a lane becomes free (PluginHooks.LaneFreed for the light one).
    **Start never calls them**: a start refused after its lane was reserved
    gives the lane back without onLaneFreed (a hook held meanwhile waits
    for the next free lane), and a run cancelled while being prepared is
    returned by Start ended (CANCELLED) without onRunEnded.

    A hook's run: its job folder is removed when it ends, except the last
    failed one of each hook (request.json and worker.log kept for 'see the
    log'; replaced by the next failure). Its runs do not count towards
    RUNS_KEPT_PER_PLUGIN; the hook's history (PluginHooks) keeps the
    summary."""

    def Start(self, pluginId: str, jobId: str, trigger: Trigger,
              params: Mapping[str, Any], settings: Mapping[str, Any],
              picks: Mapping[str, InputPick]) -> Union[RunRecord, Refused]:
        """Start a run (manual, action or hook) or say why not. ``params``
        are the job's value fields (file / folder already resolved paths),
        ``settings`` PluginSettings.Get's. Calls neither callback (above);
        a run cancelled while being prepared comes back as its record,
        CANCELLED. UNAVAILABLE after Shutdown, or when the host cannot write
        the run's folder (detail)."""

    def BusyFor(self, lane: Lane) -> Optional[Refused]:
        """BUSY (with the remaining time when known) if ``lane`` is full,
        None when free."""

    def Claim(self, lane: Lane, owner: str) -> bool:
        """Take ``lane`` for ``owner`` (a plugin id: the built-in vodsplit
        on the heavy one): True when it was free, False when full or after
        Shutdown. Running(owner) is True while it holds it."""

    def Release(self, lane: Lane, owner: str) -> None:
        """Give back ``owner``'s claim of ``lane`` (nothing when it holds
        none); onLaneFreed on the caller's thread."""

    def Running(self, pluginId: str) -> bool:
        """Whether a run of the plugin is going (update, removal)."""

    def Cancel(self, pluginId: str, runId: str) -> bool:
        """Ask a run to stop; False when it is not running."""

    def Get(self, pluginId: str, runId: str) -> Optional[RunRecord]:
        """A run's record."""

    def List(self, pluginId: str) -> Sequence[RunRecord]:
        """The plugin's kept runs, newest first."""

    def LogTail(self, pluginId: str, runId: str,
                maxBytes: int) -> Optional[str]:
        """The end of a run's worker.log."""

    def ArtifactFile(self, pluginId: str, runId: str,
                     index: int) -> Optional[Tuple[str, ArtifactKind]]:
        """(absolute path, kind) of an accepted artifact, checked again
        (ArtifactRejection), or None."""

    def Sweep(self) -> None:
        """At launch: runs left running -> FAILED (AUTONA_EXITED)."""

    def Shutdown(self, grace: float) -> None:
        """The shells' close: refuse new starts and claims, cancel this
        module's runs, wait up to ``grace`` seconds, then RunHandle.Kill
        the ones still running. A claimed lane is its owner's to stop (never
        the runner's Shutdown: it would stop vodsplit too)."""


class PluginHooks(Protocol):
    """The hooks' dispatcher (lane L9; ADR-009 D5, appendix D): one thread,
    a pending slot per hook, the 60 s and 1 h windows, the auto-disable,
    the history. Gets triggers queued from the match log's observer and the
    result render lane; subscribes to no state. Never runs headless. An
    auto-disabled hook shows on its row (HookStatus), never in the list's
    state."""

    def Start(self) -> None:
        """Read the enabled hooks and start the dispatcher thread (not
        headless)."""

    def Shutdown(self) -> None:
        """Stop it and drop what is pending; no start after it. Not called
        holding StateManager.lock."""

    def Refresh(self) -> None:
        """Read the hooks' records again (after the registry's Scan -- not
        from inside it, nor holding its lock or StateManager.lock)."""

    def Wanted(self, event: HookEvent) -> bool:
        """Whether any enabled hook is on ``event`` (else the trigger is
        not queued at all). Called inside StateManager.lock: no I/O, leaf
        locks only."""

    def MatchCandidate(self, recordId: str,
                       line: Mapping[str, Any]) -> None:
        """match_decided's candidate. Called inside StateManager.lock: no
        I/O, leaf locks only (it only queues). Fires
        MATCH_DECIDED_HOLD_SECONDS after the record's first
        candidate unless withdrawn; called again for the same record before
        that, it replaces the line only (the wait is not restarted). The
        dispatcher keeps the 'fired' mark per record: a candidate of a
        record that fired is ignored."""

    def MatchWithdrawn(self, recordId: str) -> None:
        """The candidate no longer holds. Called inside StateManager.lock:
        no I/O, leaf locks only (it only queues). The wait is cancelled,
        the record is not marked fired."""

    def ResultImageSaved(self, path: str) -> None:
        """One result image saved to out/results (only queues)."""

    def LaneFreed(self) -> None:
        """The light lane is free: start the oldest pending hook."""

    def RunEnded(self, pluginId: str, hookKey: str,
                 record: RunRecord) -> None:
        """A hook's run ended: count failures, history, the last error."""

    def SetEnabled(self, pluginId: str, hookKey: str, enabled: bool,
                   params: Mapping[str, Any]) -> Union[HookStatus, Refused]:
        """Enable (with the values it runs with; a file / folder value as a
        pick id) or disable a hook."""

    def Status(self, pluginId: str) -> Sequence[HookStatus]:
        """The plugin's hooks."""

    def Forget(self, pluginId: str,
               reason: HookDisabledReason) -> None:
        """Disable every hook of the plugin and drop its pending ones, for
        ``reason``: NEW_INSTALL (values back to the defaults, failures 0,
        history and last error cleared: another plugin now), RECONSENT
        (values back to the defaults, failures 0), BLOCKED, REMOVED (before
        its user_data folder is removed). Not for a plugin's disable: its
        pending hooks are discarded when they would start (DISABLED).
        PARAMS_CHANGED is the dispatcher's own, per hook: a saved value no
        longer fits the job (an update changed its fields), or the start
        was refused INVALID_REQUEST."""

    def TrySample(self, pluginId: str,
                  hookKey: str) -> Union[RunRecord, Refused]:
        """A dev plugin's 'try with the sample input' (D.6): one start with
        the golden input of the hook's event (contract_1.json field_types
        source[].golden), counted in neither window nor the failures.
        INVALID_REQUEST for a plugin that is not a dev one; UNAVAILABLE
        without the sample file, headless or after Shutdown."""


class PluginInstaller(Protocol):
    """Installs (lane L4; ADR-009 D7, appendix C): the zip's checks, the
    source decision, the consent digest, the blocklist, the swap and the
    removal."""

    def CheckZip(self, zipPath: str,
                 descriptor: Optional[Mapping[str, Any]]) -> Sequence[str]:
        """The zip's problems (ZipProblem values; empty when fine): the C.1
        rules, and the release descriptor (autona-plugin.json) when
        given."""

    def DecideSource(self, pluginId: str,
                     installed: Optional[InstallRecord],
                     incoming: InstallSource, incomingVersion: str, *,
                     devIdExists: bool) -> InstallDecision:
        """The C.3 table (taguyan. against OFFICIAL_REPO_IDS). Pure."""

    def ConsentDigest(self, manifest: Manifest,
                      source: InstallSource) -> str:
        """One digest of what consent shows (declarations, source types,
        secret names, hooks, the source): a change asks for consent again.
        Pure."""

    def BlockedBy(self, pluginId: str, version: str, sha256: Optional[str],
                  repoId: Optional[int],
                  blocklist: Sequence[BlockEntry]) -> Optional[BlockEntry]:
        """The first row of ``blocklist`` the plugin matches. Pure."""

    def Remove(self, pluginId: str, *, data: bool, runs: str) -> None:
        """Remove a plugin (C.6): ``runs`` is delete / keep_out / keep."""

    def Sweep(self) -> None:
        """At launch: the swaps' leftovers (.new / .old / .part)."""


class ReleaseNetwork(Protocol):
    """GitHub as C.3 reads it, given to PluginInstaller (the wiring makes
    it; it is never called with a lock held). Any exception is a network
    failure."""

    def RepoName(self, repoId: int) -> str:
        """``GET api.github.com/repositories/<repoId>`` -> its
        ``owner/repo`` now."""

    def RepoId(self, name: str) -> int:
        """``GET api.github.com/repos/<name>`` -> its numeric id."""

    def LatestDescriptor(self, name: str) -> bytes:
        """``https://github.com/<name>/releases/latest/download/
        autona-plugin.json`` (at most RELEASE_DESCRIPTOR_MAX_BYTES)."""

    def Download(self, name: str, tag: str, zipName: str, target: str,
                 size: int) -> None:
        """``https://github.com/<name>/releases/download/<tag>/<zipName>``
        to ``target`` (``<id>.part``; resumed when it is there), ``size``
        bytes."""


# What a run's job folder holds (contract_1.json manifest_paths.job_files).
REQUEST_FILE = "request.json"
JOB_FILE = "job.json"
LOG_FILE = "worker.log"
INPUTS_DIR = "inputs"
OUT_DIR = "out"
MANIFEST_FILE = "plugin.json"
