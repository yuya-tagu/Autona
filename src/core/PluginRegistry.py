"""The plugins Autona knows (ADR-009 D3/D4/D9, appendix B; lane L2): reads
and checks every manifest, keeps the list, judges ``describe`` answers.

Where plugins come from (``Scan``):

- the built-in vodsplit (D9): a manifest made here (``BUILTIN_ID``, lane
  heavy, no time limit -- ``timeout_seconds`` None, which PluginTypes'
  ``Job`` is to allow --, manual only: its own page) that points at the
  folder ArchiveWorker already uses (``ArchiveWorker.WORKER_DIR`` under the
  Autona folder). Never checked against the schema, never disabled or
  blocked; its own way of starting (``version`` for describe,
  ``--cancel-on-stdin``) is ``BUILTIN_VODSPLIT``;
- the installed ones: ``<Autona folder>/plugins/<id>/plugin.json`` (the
  folder's name is the id; ``<id>.new`` / ``<id>.old`` folders -- a swap's
  -- are skipped);
- the dev folders (D4): ``user_data/plugins/dev.json``'s list while its
  toggle is on, and ``AUTONA_PLUGIN_DEV`` (``;``-separated, always).
  ``DevFolders`` names dev.json's while the toggle is off too
  (``DevFolderActive``). A broken dev.json is moved aside before the next
  write, never overwritten. A dev
  folder is named by ``DevFolderId`` (the first 12 hex digits of the
  SHA-256 of its normalised absolute path), never by its position.

An id has one owner: the built-in, then the installed ones, then the dev
folders in order. A dev folder whose id is taken is refused (``id_taken``,
``DevFolderProblems``) and is not listed: both entrances (``AddDevFolder``
here, the installer there) refuse the later one, so the two meet only when
someone copied a folder by hand, and then the installed one stays. A
refused folder is in ``RefusedFolders`` (a list row's id names one plugin).
A manifest that breaks the check itself is its folder's
``manifest_unreadable``, never the scan's end; one Scan runs at a time.

``Check`` is the manifest's whole check (contract_1.json manifest_checks):
plugin.schema.json written out in Python (jsonschema is not a dependency)
with the contract's codes -- the top level and ``settings`` make the plugin
unusable, a job only itself -- and what the schema cannot say (the id
against its folder, the ``taguyan.`` reservation, entry and icon inside
the folder, key uniqueness, the B.4 fill rules, defaults). A name this host
does not know (a field type, a hook's on, an action's target) makes the
job unusable (in ``settings``: the plugin), never the manifest (D8).

The manifest is built leniently: a field that fails the check takes a
neutral value so the list can still show the name and the author; whether
it may run is the problems' say (``PluginUsable`` / ``UsableJobs``).

The list's state is computed when asked (``Entries`` / ``Get``), first of
``STATE_PRECEDENCE``: blocked (``blockedBy``; never a dev or the built-in),
incompatible (a plugin problem, a failed describe, or no usable job),
disabled (``isEnabled``; never the built-in), running (``isRunning``),
needs_settings (``settingsMissing``), else ready. The blocklist, the
enables, the installs' sources, the runs and the settings belong to other
parts: they are injected callables, called outside this registry's lock.

``describe`` runs only through the injected ``describe`` callable
(``PluginRunner.Describe``'s shape) and only on ``Reload`` of a dev plugin;
nothing starts at launch. Results live in memory for the same folder, id
and version.

Qt-free; nothing is read, written or made at import.
"""

import dataclasses
import hashlib
import json
import os
import re
import shutil
import threading
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from loguru import logger

from . import ArchiveWorker
from .PluginTypes import (
    ACTION_FILLS, CONTEXT_ONLY_TYPES, FIELD_KEY_PATTERN,
    FILE_EXTENSION_PATTERN, HEAVY_TIMEOUT_SECONDS, HOOK_FILLS,
    HOOK_JOB_MEMORY_MB_MAX, HOST_PLATFORM, HOST_READS_MANIFESTS,
    HOST_SPEAKS_PROTOCOLS, ICON_MAX_BYTES, JOB_ID_PATTERN, LIGHT_MEMORY_MB,
    LIGHT_TIMEOUT_SECONDS, MANIFEST_FILE, MANIFEST_MAX_BYTES, NAME_PATTERN,
    PLATFORM_PATTERN, PLUGIN_ID_MAX_LENGTH, PLUGIN_ID_PATTERN,
    RESERVED_ENTRY_PREFIX, RESERVED_ID_PREFIX, SELECT_MAX_OPTIONS,
    SOURCE_FIELD_TYPES, STATE_PRECEDENCE, TEXT_MAX_LENGTH, URL_MAX_LENGTH,
    VERSION_PATTERN, Action, ActionTarget, Badge, DescribeAnswer,
    DescribeFailure, DescribeResult, DescribeRun, DevFolder, Field,
    FieldType, Hook, HookEvent, InstallSource, Job, Lane, ListEntry,
    Manifest, ManifestCheck, ManifestProblem, ManifestReport, Option,
    Permissions, PluginState, ProblemScope, Refused, RegisteredPlugin,
    StartRefusal, Text, UpdateSource)

# ── the built-in vodsplit (D9) ──────────────────────────────────────────

BUILTIN_ID = "taguyan.vodsplit"
PLUGINS_DIR = "plugins"
DEV_LIST_FILE = "dev.json"
DEV_ENV = "AUTONA_PLUGIN_DEV"
# The official repositories' numeric ids (D7: a taguyan. id comes only from
# these). Empty until the first official plugin repository exists.
OFFICIAL_REPO_IDS = frozenset()
# A swap's leftovers, never a plugin (PluginInstaller sweeps them).
_SKIPPED_SUFFIXES = (".new", ".old")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# dev.json's os.replace: a reader holding it open on Windows fails it for a
# moment (StateManager._atomic_write_bytes's budget).
_REPLACE_ATTEMPTS = 4
_REPLACE_BACKOFF = 0.02


@dataclasses.dataclass(frozen=True)
class BuiltinWorker:
    """How the built-in vodsplit differs from a Worker Protocol 1 plugin
    (D9): ``describe`` is its ``version`` command, a run takes its own
    arguments plus ``--cancel-on-stdin``, no time limit (None)."""
    plugin_id: str
    executable: str
    describe_args: Tuple[str, ...]
    run_args: Tuple[str, ...]
    timeout_seconds: Optional[int]


BUILTIN_VODSPLIT = BuiltinWorker(
    plugin_id=BUILTIN_ID,
    executable=ArchiveWorker.WORKER_EXECUTABLE,
    describe_args=("version",),
    run_args=("--cancel-on-stdin",),
    timeout_seconds=None)

# The top-level keys plugin.schema.json names; another is unknown_key.
_TOP_KEYS = frozenset({
    "manifest", "id", "version", "name", "description", "author",
    "homepage", "funding", "icon", "license", "min_autona", "protocol",
    "platforms", "entry", "update", "permissions", "settings", "jobs"})
_FILES_SCOPES = ("inputs", "data", "user", "anywhere")
_API_USES = ("none", "read", "write")
_ENTRY_HEAD = re.compile(
    r"^(?!@)(?![A-Za-z]:)(?![/\\])(?!(.*[/\\])?\.\.([/\\]|$))"
    r"[^:*?\"<>|\u0000-\u001f]+$")
_HOST_NAME = re.compile(
    r"^(\*|[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*)$")
_HTTPS_URL = re.compile(r"^https://[^\s@/]+(/[^\s]*)?$")
_LOCALE = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")
_GITHUB_REPO = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")


def DevFolderId(path: str) -> str:
    """A dev folder's stable id (contract patterns.dev_folder_id_format)."""
    normal = os.path.normcase(os.path.abspath(path))
    return hashlib.sha256(normal.encode("utf-8")).hexdigest()[:12]


def ReadManifest(folder: str) -> Tuple[Any, Optional[ManifestProblem]]:
    """(the parsed plugin.json, None), or (None, MANIFEST_UNREADABLE): a
    missing file, over MANIFEST_MAX_BYTES, not UTF-8 (a BOM is accepted),
    not JSON. Whether it is an object is Check's."""
    path = os.path.join(folder, MANIFEST_FILE)
    try:
        with open(path, "rb") as file:
            data = file.read(MANIFEST_MAX_BYTES + 1)
    except OSError as error:
        return None, _Unreadable(f"cannot read {MANIFEST_FILE}: "
                                 f"{type(error).__name__}")
    if len(data) > MANIFEST_MAX_BYTES:
        return None, _Unreadable(f"over {MANIFEST_MAX_BYTES} bytes")
    try:
        return json.loads(data.decode("utf-8-sig")), None
    except UnicodeDecodeError:
        return None, _Unreadable("not UTF-8")
    except ValueError as error:
        return None, _Unreadable(f"not JSON: {getattr(error, 'msg', error)}")
    except RecursionError:
        return None, _Unreadable("nested too deep")


def _Unreadable(detail):
    return ManifestProblem(ManifestCheck.MANIFEST_UNREADABLE,
                           ProblemScope.PLUGIN, MANIFEST_FILE, None, detail)


def PluginUsable(report: ManifestReport) -> bool:
    """No PLUGIN-scope problem and a manifest."""
    return report.manifest is not None and not any(
        problem.scope == ProblemScope.PLUGIN for problem in report.problems)


def UsableJobs(report: ManifestReport) -> Tuple[Job, ...]:
    """The jobs no JOB problem names (none when the plugin is unusable)."""
    if not PluginUsable(report):
        return ()
    bad = {problem.job for problem in report.problems
           if problem.scope == ProblemScope.JOB}
    return tuple(job for job in report.manifest.jobs if job.id not in bad)


def _Version(text):
    if not isinstance(text, str) or not re.fullmatch(VERSION_PATTERN, text):
        return None
    try:
        return tuple(int(part) for part in text.split("."))
    except ValueError:  # past int()'s digit limit
        return None


# ── the value checks (plugin.schema.json's keywords) ────────────────────

def _IsInt(value):
    """JSON Schema's integer: 1.0 is one too (bool is not)."""
    if isinstance(value, bool):
        return False
    return isinstance(value, int) or (isinstance(value, float)
                                      and value.is_integer())


def _AsInt(value, fallback=None):
    return int(value) if _IsInt(value) else fallback


def _ReservedDeviceName(part):
    """A Windows device name (con, nul, aux, prn, com1-9, lpt1-9): no file
    or folder can be named by it, with or without an extension."""
    return re.fullmatch(r"(con|nul|aux|prn|com[1-9]|lpt[1-9])", part) is not None


def _IsNumber(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _IsText(value, maxLength=None, minLength=1):
    """A text (文言): a string, or a locale map with at least one entry."""
    def good(text):
        return (isinstance(text, str) and len(text) >= minLength
                and (maxLength is None or len(text) <= maxLength))
    if isinstance(value, str):
        return good(value)
    if isinstance(value, dict):
        return bool(value) and all(
            isinstance(key, str) and _LOCALE.fullmatch(key) and good(text)
            for key, text in value.items())
    return False


def _IsUniqueList(value):
    if not isinstance(value, list):
        return False
    seen = []
    for item in value:
        if item in seen:
            return False
        seen.append(item)
    return True


def _Inside(base, path):
    """Whether ``path`` resolves (links followed) inside ``base``."""
    try:
        real = os.path.normcase(os.path.realpath(path))
        root = os.path.normcase(os.path.realpath(base))
        return os.path.commonpath([real, root]) == root and real != root
    except (OSError, ValueError):
        return False


def _FolderSize(folder):
    """The bytes of the files under ``folder``; links and junctions are not
    followed (one pointing at C:\\ is not the plugin's size)."""
    total, stack = 0, [folder]
    while stack:
        try:
            entries = list(os.scandir(stack.pop()))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink() or getattr(entry, "is_junction",
                                                 lambda: False)():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                pass
    return total


class _Problems:
    """Collects problems; ``plugin`` / ``job`` / ``warning`` add one."""

    def __init__(self):
        self.items: List[ManifestProblem] = []

    def plugin(self, code, path="", detail=""):
        self.items.append(ManifestProblem(code, ProblemScope.PLUGIN, path,
                                          None, detail))

    def job(self, code, job, path="", detail=""):
        self.items.append(ManifestProblem(code, ProblemScope.JOB, path, job,
                                          detail))

    def warning(self, code, path="", detail=""):
        self.items.append(ManifestProblem(code, ProblemScope.WARNING, path,
                                          None, detail))


class PluginRegistry:
    """PluginTypes.PluginRegistry (see the module docstring).

    ``appDir`` is the Autona folder (``plugins/`` and the built-in's folder
    are under it), ``userDataDir`` the ``user_data`` folder (``plugins/
    dev.json``). The callables (each optional, called outside the lock):

    - ``describe(pluginDir, entry, *, dev) -> DescribeRun``: PluginRunner.Describe;
    - ``installSource(pluginId) -> Optional[InstallSource]``: install.json's
      source (the ``taguyan.`` reservation; ``github`` shows as listed,
      anything else as manual). Default: manual;
    - ``blockedBy(pluginId, version) -> Optional[Text]``: the blocklist's
      reason. Default: never;
    - ``isEnabled(pluginId) -> bool``: default True;
    - ``isRunning(pluginId) -> bool``: default False;
    - ``settingsMissing(pluginId, manifest, *, dev) -> Sequence[str]``:
      PluginSettings.Missing. Default: none;
    - ``builtinVersion() -> Optional[str]``: the vodsplit worker's version
      the archive page last probed. Default: unknown ("");
    - ``environ() -> Mapping[str, str]``: default ``os.environ``;
    - ``which(name) -> Optional[str]``: a dev entry's PATH lookup.
    """

    def __init__(self, appDir: str, userDataDir: str, hostVersion: str, *,
                 describe: Optional[Callable[..., DescribeRun]] = None,
                 installSource: Optional[Callable[[str], Optional[InstallSource]]] = None,
                 blockedBy: Optional[Callable[[str, Optional[str]], Optional[Text]]] = None,
                 isEnabled: Optional[Callable[[str], bool]] = None,
                 isRunning: Optional[Callable[[str], bool]] = None,
                 settingsMissing: Optional[Callable[..., Sequence[str]]] = None,
                 builtinVersion: Optional[Callable[[], Optional[str]]] = None,
                 environ: Optional[Callable[[], Mapping[str, str]]] = None,
                 which: Optional[Callable[[str], Optional[str]]] = None,
                 officialRepoIds=OFFICIAL_REPO_IDS):
        self._appDir = os.path.abspath(appDir)
        self._userDataDir = os.path.abspath(userDataDir)
        self._hostVersion = hostVersion
        self._describe = describe
        self._installSource = installSource or (lambda _id: None)
        self._blockedBy = blockedBy or (lambda _id, _version: None)
        self._isEnabled = isEnabled or (lambda _id: True)
        self._isRunning = isRunning or (lambda _id: False)
        self._settingsMissing = settingsMissing or (lambda *_a, **_k: ())
        self._builtinVersion = builtinVersion or (lambda: "")
        self._environ = environ or (lambda: os.environ)
        self._which = which or shutil.which
        self._officialRepoIds = frozenset(officialRepoIds)
        # Lock order: _scanLock -> _lock, _devLock -> _lock. _scanLock and
        # _devLock are never held together (Scan runs after a dev.json write).
        self._lock = threading.Lock()
        # One Scan at a time: a slower earlier one never overwrites a later.
        self._scanLock = threading.Lock()
        # dev.json's read-modify-write.
        self._devLock = threading.Lock()
        # (normcase(folder), st_ino, st_mtime_ns) -> bytes
        self._sizes: Dict[Tuple[str, int, int], int] = {}
        self._activeDevIds: frozenset = frozenset()
        self._refused: Tuple[Tuple[str, Tuple[ManifestProblem, ...]], ...] = ()
        # id -> RegisteredPlugin (state is computed when asked), in order.
        self._plugins: Dict[str, RegisteredPlugin] = {}
        self._devFolders: Tuple[DevFolder, ...] = ()
        self._devProblems: Dict[str, Tuple[ManifestProblem, ...]] = {}
        # normcase(folder) -> (id, version, DescribeResult)
        self._describes: Dict[str, Tuple[str, str, DescribeResult]] = {}

    # ── paths ──────────────────────────────────────────────────────────

    def PluginsRoot(self) -> str:
        return os.path.join(self._appDir, PLUGINS_DIR)

    def BuiltinDir(self) -> str:
        return os.path.join(self._appDir, ArchiveWorker.WORKER_DIR)

    def _DevListPath(self):
        return os.path.join(self._userDataDir, PLUGINS_DIR, DEV_LIST_FILE)

    # ── the manifest's check ───────────────────────────────────────────

    def Check(self, raw: Any, folder: str, source: Optional[InstallSource],
              *, dev: bool, hostVersion: str) -> ManifestReport:
        found = _Problems()
        if not isinstance(raw, dict):
            found.items.append(_Unreadable("not a JSON object"))
            return ManifestReport(None, tuple(found.items))
        for key in raw:
            if key not in _TOP_KEYS:
                found.warning(ManifestCheck.UNKNOWN_KEY, str(key))
        version = raw.get("manifest")
        if not _IsInt(version):
            found.plugin(ManifestCheck.INVALID_FIELD, "manifest")
            return ManifestReport(None, tuple(found.items))
        if int(version) not in HOST_READS_MANIFESTS:
            found.plugin(ManifestCheck.MANIFEST_VERSION_UNKNOWN, "manifest",
                         str(version))
            return ManifestReport(None, tuple(found.items))
        pluginId = raw.get("id")
        if not (isinstance(pluginId, str)
                and re.fullmatch(PLUGIN_ID_PATTERN, pluginId)
                and len(pluginId) <= PLUGIN_ID_MAX_LENGTH):
            found.plugin(ManifestCheck.INVALID_FIELD, "id")
            return ManifestReport(None, tuple(found.items))
        if any(_ReservedDeviceName(part) for part in pluginId.split(".")):
            # plugins/<id>/ and user_data/plugins/<id>/ could not be made.
            found.plugin(ManifestCheck.INVALID_FIELD, "id",
                         "a Windows device name")
            return ManifestReport(None, tuple(found.items))
        if not dev and os.path.basename(os.path.normpath(folder)) != pluginId:
            found.plugin(ManifestCheck.ID_FOLDER_MISMATCH, "id")
        if pluginId.startswith(RESERVED_ID_PREFIX) and not dev and not (
                source is None or (source.kind == "github"
                                   and source.repo_id in self._officialRepoIds)):
            found.plugin(ManifestCheck.ID_RESERVED, "id")

        self._CheckTop(raw, folder, dev, hostVersion, found)
        settings = self._CheckFields(raw.get("settings", []), "settings",
                                     found, None)
        jobs = self._CheckJobs(raw.get("jobs"), found)
        manifest = self._Build(raw, pluginId, settings, jobs)
        return ManifestReport(manifest, tuple(found.items))

    def _CheckTop(self, raw, folder, dev, hostVersion, found):
        invalid = ManifestCheck.INVALID_FIELD
        if _Version(raw.get("version")) is None:
            found.plugin(invalid, "version")
        if not _IsText(raw.get("name"), 80):
            found.plugin(invalid, "name")
        if "description" in raw and not _IsText(raw["description"], 500, 0):
            found.plugin(invalid, "description")
        if not (isinstance(raw.get("author"), str) and raw["author"]):
            found.plugin(invalid, "author")
        for key in ("homepage", "funding"):
            if key in raw and not (isinstance(raw[key], str)
                                   and len(raw[key]) <= URL_MAX_LENGTH
                                   and _HTTPS_URL.fullmatch(raw[key])):
                found.plugin(invalid, key)
        if not (isinstance(raw.get("license"), str) and raw["license"]):
            found.plugin(invalid, "license")
        minimum = _Version(raw.get("min_autona"))
        if minimum is None:
            found.plugin(invalid, "min_autona")
        else:
            host = _Version(hostVersion)
            if host is not None and minimum > host:
                found.plugin(ManifestCheck.MIN_AUTONA_NEWER, "min_autona",
                             raw["min_autona"])

        protocol = raw.get("protocol")
        if not (_IsUniqueList(protocol) and protocol
                and all(_IsInt(item) and item >= 1 for item in protocol)):
            found.plugin(invalid, "protocol")
        elif not set(protocol) & set(HOST_SPEAKS_PROTOCOLS):
            found.plugin(ManifestCheck.PROTOCOL_UNSUPPORTED, "protocol")

        platforms = raw.get("platforms")
        if not (_IsUniqueList(platforms) and platforms and all(
                isinstance(item, str) and re.fullmatch(PLATFORM_PATTERN, item)
                for item in platforms)):
            found.plugin(invalid, "platforms")
        elif HOST_PLATFORM not in platforms:
            found.plugin(ManifestCheck.PLATFORM_UNSUPPORTED, "platforms")

        self._CheckEntry(raw.get("entry"), folder, dev, found)

        if "icon" in raw:
            if not (isinstance(raw["icon"], str) and raw["icon"]):
                found.plugin(invalid, "icon")
            else:
                why = self._IconProblem(folder, raw["icon"])
                if why:
                    found.warning(ManifestCheck.ICON_INVALID, "icon", why)

        if "update" in raw:
            update = raw["update"]
            if not (isinstance(update, dict) and isinstance(update.get("github"), str)
                    and _GITHUB_REPO.fullmatch(update["github"])):
                found.plugin(invalid, "update")

        permissions = raw.get("permissions")
        if not isinstance(permissions, dict):
            found.plugin(invalid, "permissions")
        else:
            network = permissions.get("network")
            if not (_IsUniqueList(network) and all(
                    isinstance(host, str) and _HOST_NAME.fullmatch(host)
                    for host in network)):
                found.plugin(invalid, "permissions.network")
            if permissions.get("files") not in _FILES_SCOPES:
                found.plugin(invalid, "permissions.files")
            if not isinstance(permissions.get("spawn"), bool):
                found.plugin(invalid, "permissions.spawn")
            if permissions.get("autona_api") not in _API_USES:
                found.plugin(invalid, "permissions.autona_api")
            if "note" in permissions and not _IsText(permissions["note"]):
                found.plugin(invalid, "permissions.note")

        if "settings" in raw and not isinstance(raw["settings"], list):
            found.plugin(invalid, "settings")
        jobs = raw.get("jobs")
        if not (isinstance(jobs, list) and jobs
                and all(isinstance(job, dict) for job in jobs)):
            found.plugin(invalid, "jobs")
        else:
            # Two jobs of one id would share their hook keys and runs: the
            # schema cannot say it, the manifest is what is wrong.
            ids = [job.get("id") for job in jobs]
            if len(ids) != len(set(map(repr, ids))):
                found.plugin(invalid, "jobs", "a job id twice")

    def _CheckEntry(self, entry, folder, dev, found):
        if not (isinstance(entry, list) and entry
                and all(isinstance(word, str) for word in entry)):
            found.plugin(ManifestCheck.INVALID_FIELD, "entry")
            return
        head = entry[0]
        if head.startswith(RESERVED_ENTRY_PREFIX):
            found.plugin(ManifestCheck.ENTRY_RESERVED, "entry[0]")
            return
        if not _ENTRY_HEAD.fullmatch(head):
            found.plugin(ManifestCheck.INVALID_FIELD, "entry[0]")
            return
        path = os.path.join(folder, head)
        bare = not re.search(r"[/\\]", head)
        if dev and bare and not os.path.isfile(path):
            # A dev folder may name a command on PATH (python, node).
            if not self._which(head):
                found.plugin(ManifestCheck.ENTRY_MISSING, "entry[0]",
                             "not in the folder nor on PATH")
            return
        if not _Inside(folder, path):
            found.plugin(ManifestCheck.ENTRY_OUTSIDE, "entry[0]")
        elif not os.path.isfile(path):
            found.plugin(ManifestCheck.ENTRY_MISSING, "entry[0]")
        elif not dev and not head.lower().endswith(".exe"):
            found.plugin(ManifestCheck.ENTRY_MISSING, "entry[0]",
                         "not an .exe")

    @staticmethod
    def _IconProblem(folder, icon):
        if not icon.lower().endswith(".png") or not _ENTRY_HEAD.fullmatch(icon):
            return "not a relative .png path"
        path = os.path.join(folder, icon)
        if not _Inside(folder, path):
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

    def _CheckFields(self, fields, where, found, job):
        """Check ``params`` (``job`` names the job) or ``settings`` (job
        None: every problem is the plugin's). Returns the Fields built
        leniently from the dict items."""
        if not isinstance(fields, list):
            return ()
        base = where if job is None else f"{job[1]}.params"
        jobId = None if job is None else job[0]

        def report(code, path, detail=""):
            if job is None:
                found.plugin(code, path, detail)
            else:
                found.job(code, jobId, path, detail)

        invalid = (ManifestCheck.INVALID_FIELD if job is None
                   else ManifestCheck.INVALID_JOB_FIELD)
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
                report(ManifestCheck.KEY_DUPLICATE, f"{path}.key", key)
            else:
                keys.add(key)
            if not (isinstance(kind, str) and re.fullmatch(NAME_PATTERN, kind)):
                report(invalid, f"{path}.type")
                kind = None
            if not _IsText(item.get("label")):
                report(invalid, f"{path}.label")
            if "required" in item and not isinstance(item["required"], bool):
                report(invalid, f"{path}.required")
            if "help" in item and not _IsText(item["help"]):
                report(invalid, f"{path}.help")
            if "max_length" in item and not (_IsInt(item["max_length"])
                                             and item["max_length"] >= 1):
                report(invalid, f"{path}.max_length")
            for extra in ("min", "max"):
                if extra in item and not _IsNumber(item[extra]):
                    report(invalid, f"{path}.{extra}")
            if "step" in item and not (_IsNumber(item["step"]) and item["step"] > 0):
                report(invalid, f"{path}.step")
            for extra, owner in (("options", FieldType.SELECT),
                                 ("extensions", FieldType.FILE)):
                # The owning type's own check reports it (once).
                if (extra in item and not isinstance(item[extra], list)
                        and kind != owner):
                    report(invalid, f"{path}.{extra}")
            if kind is not None:
                self._CheckType(item, kind, path, where, report, invalid)
            built.append(self._BuildField(item, kind))
        return tuple(built)

    @staticmethod
    def _CheckType(item, kind, path, where, report, invalid):
        known = {member.value for member in FieldType}
        if kind not in known:
            report(ManifestCheck.FIELD_TYPE_UNKNOWN, f"{path}.type", kind)
            return
        if kind == FieldType.SECRET and where == "params":
            report(ManifestCheck.SECRET_OUTSIDE_SETTINGS, f"{path}.type")
            return
        if kind in SOURCE_FIELD_TYPES:
            if where == "settings":
                report(ManifestCheck.SOURCE_IN_SETTINGS, f"{path}.type", kind)
            return  # a source field's default is ignored
        has = "default" in item
        default = item.get("default")
        bad = ManifestCheck.DEFAULT_INVALID
        if kind in (FieldType.TEXT, FieldType.TEXTAREA):
            limit = item.get("max_length")
            if _IsInt(limit) and limit > TEXT_MAX_LENGTH["cap"]:
                report(invalid, f"{path}.max_length")
            if has:
                if not _IsInt(limit) or limit < 1:
                    limit = TEXT_MAX_LENGTH[f"{kind}_default"]
                if not isinstance(default, str) or len(default) > limit:
                    report(bad, f"{path}.default")
        elif kind == FieldType.NUMBER:
            if has and (not _IsNumber(default)
                        or (_IsNumber(item.get("min")) and default < item["min"])
                        or (_IsNumber(item.get("max")) and default > item["max"])):
                report(bad, f"{path}.default")
        elif kind == FieldType.BOOL:
            if has and not isinstance(default, bool):
                report(bad, f"{path}.default")
        elif kind == FieldType.SELECT:
            options = item.get("options")
            good = (isinstance(options, list)
                    and 1 <= len(options) <= SELECT_MAX_OPTIONS and all(
                        isinstance(option, dict)
                        and isinstance(option.get("value"), str)
                        and option["value"] and _IsText(option.get("label"))
                        for option in options))
            if not good:
                report(invalid, f"{path}.options")
            elif has and default not in [o["value"] for o in options]:
                report(bad, f"{path}.default")
        elif kind == FieldType.FILE:
            extensions = item.get("extensions")
            if not (_IsUniqueList(extensions) and extensions and all(
                    isinstance(ext, str) and re.fullmatch(FILE_EXTENSION_PATTERN, ext)
                    for ext in extensions)):
                report(invalid, f"{path}.extensions")
            if has and not isinstance(default, str):
                report(bad, f"{path}.default")
        elif kind == FieldType.FOLDER:
            if has and not isinstance(default, str):
                report(bad, f"{path}.default")
        elif kind == FieldType.SECRET:
            if has:
                report(invalid, f"{path}.default", "a secret has no default")

    def _CheckJobs(self, jobs, found):
        if not isinstance(jobs, list):
            return ()
        built = []
        for index, raw in enumerate(jobs):
            if isinstance(raw, dict):
                built.append(self._CheckJob(raw, f"jobs[{index}]", found))
        return tuple(built)

    def _CheckJob(self, raw, path, found):
        jobId = raw.get("id")
        name = jobId if isinstance(jobId, str) else path
        invalid = ManifestCheck.INVALID_JOB_FIELD

        def problem(code, where="", detail=""):
            found.job(code, name, f"{path}.{where}" if where else path, detail)

        if not (isinstance(jobId, str) and re.fullmatch(JOB_ID_PATTERN, jobId)):
            problem(invalid, "id")
        if not _IsText(raw.get("title")):
            problem(invalid, "title")
        lane = raw.get("lane")
        if lane not in (Lane.LIGHT, Lane.HEAVY):
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
        if not _IsInt(timeout):
            problem(invalid, "timeout_seconds")
        else:
            low, high = (LIGHT_TIMEOUT_SECONDS if lane == Lane.LIGHT
                         else HEAVY_TIMEOUT_SECONDS)
            if not low <= timeout <= high:
                problem(ManifestCheck.TIMEOUT_OUT_OF_RANGE, "timeout_seconds",
                        str(timeout))
        memory = raw.get("memory_mb")
        if "memory_mb" in raw and not _IsInt(memory):
            problem(invalid, "memory_mb")
        elif lane == Lane.LIGHT:
            if memory is None:
                problem(invalid, "memory_mb", "a light job needs memory_mb")
            else:
                low, high = LIGHT_MEMORY_MB
                if hooks:
                    high = min(high, HOOK_JOB_MEMORY_MB_MAX)
                if not low <= memory <= high:
                    problem(ManifestCheck.MEMORY_OUT_OF_RANGE, "memory_mb",
                            str(memory))
        if "manual" in raw and not isinstance(raw["manual"], bool):
            problem(invalid, "manual")
        if "params" in raw and not isinstance(raw["params"], list):
            problem(invalid, "params")
        params = self._CheckFields(raw.get("params", []), "params", found,
                                   (name, path))

        builtActions = []
        for index, action in enumerate(actions):
            where = f"actions[{index}]"
            if not (isinstance(action, dict) and isinstance(action.get("target"), str)
                    and re.fullmatch(NAME_PATTERN, action["target"])
                    and _IsText(action.get("label"))):
                problem(invalid, where)
                continue
            if action["target"] not in ACTION_FILLS:
                problem(ManifestCheck.ACTION_TARGET_UNKNOWN, f"{where}.target",
                        action["target"])
            builtActions.append(Action(action["target"], action["label"]))
        builtHooks = []
        seen = set()
        for index, hook in enumerate(hooks):
            where = f"hooks[{index}]"
            if not (isinstance(hook, dict) and isinstance(hook.get("on"), str)
                    and re.fullmatch(NAME_PATTERN, hook["on"])
                    and _IsText(hook.get("label"))):
                problem(invalid, where)
                continue
            if hook["on"] not in HOOK_FILLS:
                problem(ManifestCheck.HOOK_EVENT_UNKNOWN, f"{where}.on",
                        hook["on"])
            if hook["on"] in seen:
                problem(ManifestCheck.HOOK_DUPLICATE, f"{where}.on", hook["on"])
            seen.add(hook["on"])
            builtHooks.append(Hook(hook["on"], hook["label"]))

        if (builtActions or builtHooks) and lane != Lane.LIGHT:
            problem(ManifestCheck.TRIGGER_NEEDS_LIGHT_LANE, "lane")
        types = [field.type for field in params]
        manual = raw.get("manual", True)
        if any(kind in CONTEXT_ONLY_TYPES for kind in types) and manual is not False:
            problem(ManifestCheck.MANUAL_WITH_CONTEXT, "manual")
        self._CheckFills(params, builtActions, builtHooks, problem)
        if builtHooks and isinstance(raw.get("params"), list):
            for index, item in enumerate(raw["params"]):
                if (isinstance(item, dict) and item.get("required") is True
                        and isinstance(item.get("type"), str)
                        and item["type"] not in SOURCE_FIELD_TYPES
                        and "default" not in item):
                    problem(ManifestCheck.HOOK_PARAM_NEEDS_DEFAULT,
                            f"params[{index}].default", str(item.get("key")))
        if manual is False and not builtActions and not builtHooks:
            found.warning(ManifestCheck.JOB_UNREACHABLE, path, name)

        return Job(
            id=name, title=raw.get("title") if _IsText(raw.get("title")) else name,
            lane=lane if isinstance(lane, str) else "",
            timeout_seconds=_AsInt(timeout, 0),
            memory_mb=_AsInt(memory),
            params=params, manual=manual if isinstance(manual, bool) else True,
            actions=tuple(builtActions), hooks=tuple(builtHooks))

    @staticmethod
    def _CheckFills(params, actions, hooks, problem):
        """ADR-009 B.4: each known trigger fills exactly one field of its
        type, every context-only field, and every required image field."""
        triggers = [(f"actions[{i}]", ACTION_FILLS[a.target])
                    for i, a in enumerate(actions) if a.target in ACTION_FILLS]
        triggers += [(f"hooks[{i}]", HOOK_FILLS[h.on])
                     for i, h in enumerate(hooks) if h.on in HOOK_FILLS]
        images = (FieldType.RESULT_IMAGE, FieldType.THUMBNAIL_IMAGE)
        for where, fills in triggers:
            count = sum(1 for field in params if field.type == fills)
            if count != 1:
                problem(ManifestCheck.FILL_COUNT, where,
                        f"{count} fields of {fills}")
            for field in params:
                if field.type == fills:
                    continue
                if field.type in CONTEXT_ONLY_TYPES or (
                        field.type in images and field.required):
                    problem(ManifestCheck.TRIGGER_UNFILLABLE, where,
                            f"cannot fill {field.key} ({field.type})")

    @staticmethod
    def _BuildField(item, kind):
        options = tuple(
            Option(option["value"], option["label"])
            for option in item.get("options") or ()
            if isinstance(option, dict) and isinstance(option.get("value"), str)
            and _IsText(option.get("label")))
        extensions = tuple(ext for ext in item.get("extensions") or ()
                           if isinstance(ext, str))
        key = item.get("key")

        def number(name):
            value = item.get(name)
            return value if _IsNumber(value) else None

        return Field(
            key=key if isinstance(key, str) else "",
            type=kind or "",
            label=item["label"] if _IsText(item.get("label")) else str(key),
            required=item.get("required") is True,
            default=(None if kind in SOURCE_FIELD_TYPES or kind == FieldType.SECRET
                     else item.get("default")),
            help=item["help"] if _IsText(item.get("help")) else None,
            max_length=_AsInt(item.get("max_length")),
            min=number("min"), max=number("max"), step=number("step"),
            options=options, extensions=extensions)

    @staticmethod
    def _Build(raw, pluginId, settings, jobs):
        def text(key, fallback=None):
            return raw[key] if _IsText(raw.get(key), None, 0) else fallback

        def string(key, fallback=None):
            value = raw.get(key)
            return value if isinstance(value, str) else fallback

        def ints(key):
            value = raw.get(key)
            return tuple(int(v) for v in value if _IsInt(v)) if isinstance(value, list) else ()

        def strings(key):
            value = raw.get(key)
            return tuple(v for v in value if isinstance(v, str)) if isinstance(value, list) else ()

        permissions = raw.get("permissions")
        permissions = permissions if isinstance(permissions, dict) else {}
        network = permissions.get("network")
        update = raw.get("update")
        return Manifest(
            manifest=int(raw["manifest"]), id=pluginId,
            version=string("version", ""), name=text("name", pluginId),
            author=string("author", ""), license=string("license", ""),
            min_autona=string("min_autona", ""), protocol=ints("protocol"),
            platforms=strings("platforms"), entry=strings("entry"),
            permissions=Permissions(
                network=tuple(h for h in network if isinstance(h, str))
                if isinstance(network, list) else (),
                files=permissions.get("files") if isinstance(permissions.get("files"), str) else "",
                spawn=permissions.get("spawn") is True,
                autona_api=(permissions.get("autona_api")
                            if isinstance(permissions.get("autona_api"), str) else "none"),
                note=permissions.get("note") if _IsText(permissions.get("note")) else None),
            jobs=jobs, description=text("description"),
            homepage=string("homepage"), funding=string("funding"),
            icon=string("icon"),
            update=(UpdateSource(update["github"])
                    if isinstance(update, dict) and isinstance(update.get("github"), str)
                    else None),
            settings=settings)

    # ── describe ───────────────────────────────────────────────────────

    def JudgeDescribe(self, run: DescribeRun,
                      manifest: Manifest) -> DescribeResult:
        """contract_1.json commands.describe: judged_by / unusable_when."""
        if not run.launched:
            return DescribeResult(None, failure=DescribeFailure.LAUNCH_FAILED,
                                  detail="entry missing" if run.entry_missing else "")
        if run.timed_out:
            return DescribeResult(None, failure=DescribeFailure.TIMED_OUT)
        if run.exit_code != 0:
            return DescribeResult(None, failure=DescribeFailure.NONZERO_EXIT,
                                  detail="no exit code" if run.exit_code is None
                                  else f"exit code {run.exit_code}")
        if run.stdout_lines == 0:
            return DescribeResult(None, failure=DescribeFailure.NO_LINE)
        if not run.objects:
            return DescribeResult(None, failure=DescribeFailure.NOT_JSON)
        line = next((obj for obj in run.objects
                     if isinstance(obj, Mapping) and obj.get("type") == "describe"),
                    None)
        if line is None:
            return DescribeResult(None, failure=DescribeFailure.TYPE_NOT_DESCRIBE)
        protocol = line.get("protocol")
        if not (isinstance(protocol, list) and protocol
                and all(_IsInt(item) for item in protocol)):
            return DescribeResult(None, failure=DescribeFailure.PROTOCOL_MISSING)
        answer = DescribeAnswer(
            protocol=tuple(int(item) for item in protocol),
            id=line.get("id") if isinstance(line.get("id"), str) else "",
            version=(line.get("version")
                     if isinstance(line.get("version"), str) else ""))
        if answer.id != manifest.id:
            return DescribeResult(answer, failure=DescribeFailure.ID_MISMATCH,
                                  detail=answer.id)
        if answer.version != manifest.version:
            return DescribeResult(answer, failure=DescribeFailure.VERSION_MISMATCH,
                                  detail=answer.version)
        common = set(answer.protocol) & set(HOST_SPEAKS_PROTOCOLS)
        if not common:
            return DescribeResult(answer, failure=DescribeFailure.NO_COMMON_PROTOCOL)
        return DescribeResult(answer, protocol=max(common))

    # ── the scan ───────────────────────────────────────────────────────

    def Scan(self) -> None:
        with self._scanLock:
            self._ScanLocked()

    def _ScanLocked(self):
        plugins: Dict[str, RegisteredPlugin] = {}
        refused: List[Tuple[str, Tuple[ManifestProblem, ...]]] = []
        builtin = self._Builtin()
        plugins[builtin.id] = builtin

        root = self.PluginsRoot()
        try:
            names = sorted(os.listdir(root))
        except OSError:
            names = []
        for name in names:
            folder = os.path.join(root, name)
            if not os.path.isdir(folder) or (
                    name.endswith(_SKIPPED_SUFFIXES)
                    and re.fullmatch(PLUGIN_ID_PATTERN, name[:-4])):
                continue
            source = (self._Call(self._installSource, None, name)
                      or InstallSource("manual"))
            report = self._CheckFolder(folder, source, dev=False)
            if name in plugins:
                # plugins/taguyan.vodsplit/: the built-in keeps the id.
                refused.append((folder, self._Taken(report).problems))
                logger.warning("Plugins: an installed folder has the built-in's id")
                continue
            plugins[name] = self._Registered(
                name, folder, Badge.LISTED if source.kind == "github" else Badge.MANUAL,
                report)

        folders, active, devPlugins, devProblems = self._ScanDev(plugins)
        plugins.update(devPlugins)
        refused += [(folder.path, devProblems[folder.id]) for folder in folders
                    if any(p.code == ManifestCheck.ID_TAKEN
                           for p in devProblems.get(folder.id, ()))]
        with self._lock:
            self._plugins = plugins
            self._devFolders = folders
            self._activeDevIds = active
            self._devProblems = devProblems
            self._refused = tuple(refused)
            live = {os.path.normcase(p.folder) for p in plugins.values()}
            self._describes = {k: v for k, v in self._describes.items()
                               if k in live}
        logger.info(f"Plugins: {len(plugins) - 1} found "
                    f"({len(devPlugins)} in dev folders)")

    def _CheckFolder(self, folder, source, *, dev):
        """ReadManifest + Check of one folder. A manifest that breaks the
        check itself is that folder's MANIFEST_UNREADABLE, never the scan's
        end."""
        try:
            raw, problem = ReadManifest(folder)
            if problem:
                return ManifestReport(None, (problem,))
            return self.Check(raw, folder, source, dev=dev,
                              hostVersion=self._hostVersion)
        except Exception as error:
            logger.warning(f"Plugins: a manifest broke the check: "
                           f"{type(error).__name__}")
            return ManifestReport(None, (_Unreadable(
                f"the check failed: {type(error).__name__}"),))

    def _Builtin(self):
        version = self._Call(self._builtinVersion, "") or ""
        job = Job(id="run", title={"ja": "アーカイブ切り抜き", "en": "Archive Clipper"},
                  lane=Lane.HEAVY,
                  # None: no time limit (Job.timeout_seconds is typed int in
                  # PluginTypes; the contract patch makes it Optional).
                  # Manual false: it runs from its own page, never the
                  # generic form.
                  timeout_seconds=BUILTIN_VODSPLIT.timeout_seconds, manual=False)
        manifest = Manifest(
            manifest=1, id=BUILTIN_ID, version=version,
            name={"ja": "アーカイブ切り抜き", "en": "Archive Clipper"},
            author="taguyan", license="see LICENSE", min_autona="",
            protocol=(1,), platforms=(HOST_PLATFORM,),
            entry=(BUILTIN_VODSPLIT.executable,),
            permissions=Permissions(network=("*",), files="anywhere",
                                    spawn=True, autona_api="none"),
            jobs=(job,))
        folder = self.BuiltinDir()
        return RegisteredPlugin(id=BUILTIN_ID, folder=folder, badge=Badge.BUILTIN,
                                report=ManifestReport(manifest, ()),
                                state=PluginState.READY,
                                size_bytes=self._Size(folder))

    def _Size(self, folder):
        """_FolderSize, kept while the folder is the same one (a swap makes
        a new folder; a file changed deep inside is not seen until then)."""
        try:
            info = os.stat(folder)
        except OSError:
            return 0
        key = (os.path.normcase(folder), info.st_ino, info.st_mtime_ns)
        with self._lock:
            size = self._sizes.get(key)
        if size is None:
            size = _FolderSize(folder)
            with self._lock:
                self._sizes = {k: v for k, v in self._sizes.items()
                               if k[0] != key[0]}
                self._sizes[key] = size
        return size

    def _Registered(self, pluginId, folder, badge, report):
        manifest = report.manifest
        hasIcon = bool(manifest and manifest.icon) and not any(
            problem.code in (ManifestCheck.ICON_INVALID, ManifestCheck.INVALID_FIELD)
            and problem.path == "icon" for problem in report.problems)
        # A dev folder is a source tree (a venv, node_modules): not walked,
        # its size is nobody's install.
        size = 0 if badge == Badge.DEV else self._Size(folder)
        return RegisteredPlugin(id=pluginId, folder=folder, badge=badge,
                                report=report, state=PluginState.READY,
                                size_bytes=size, has_icon=hasIcon)

    @staticmethod
    def _Taken(report):
        return ManifestReport(report.manifest, report.problems + (
            ManifestProblem(ManifestCheck.ID_TAKEN, ProblemScope.PLUGIN, "id"),))

    def _ReadDevList(self):
        """(toggle, [paths], broken) of dev.json; (False, [], False) when it
        is not there, (False, [], True) when it is but cannot be read as
        one (it is moved aside before the next write, never overwritten)."""
        try:
            with open(self._DevListPath(), encoding="utf-8-sig") as file:
                data = json.load(file)
        except FileNotFoundError:
            return False, [], False
        except (OSError, ValueError, RecursionError):
            return False, [], True
        if not isinstance(data, dict):
            return False, [], True
        folders = data.get("folders")
        folders = [p for p in folders if isinstance(p, str)] if isinstance(folders, list) else []
        return data.get("enabled") is True, folders, False

    def _WriteDevList(self, enabled, folders):
        """Write dev.json (a broken one moved aside first). OSError when it
        cannot be written; the temporary file is not left behind."""
        path = self._DevListPath()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if self._ReadDevList()[2]:
            aside = f"{path}.unreadable-{time.strftime('%Y%m%d-%H%M%S')}.bak"
            os.replace(path, aside)
            logger.warning("Plugins: dev.json was unreadable; moved aside")
        temp = path + ".tmp"
        try:
            with open(temp, "w", encoding="utf-8") as file:
                json.dump({"enabled": bool(enabled), "folders": list(folders)},
                          file, ensure_ascii=False, indent=2)
            delay = _REPLACE_BACKOFF
            for attempt in range(_REPLACE_ATTEMPTS):
                try:
                    os.replace(temp, path)
                    return
                except OSError:  # a reader holding it open on Windows
                    if attempt == _REPLACE_ATTEMPTS - 1:
                        raise
                    time.sleep(delay)
                    delay *= 2
        finally:
            if os.path.exists(temp):
                try:
                    os.remove(temp)
                except OSError:
                    pass

    def _DevPaths(self):
        """[(path, from_env, active)] of every dev folder, each once:
        dev.json's (active while its toggle is on), then AUTONA_PLUGIN_DEV's
        (always active)."""
        enabled, listed, _broken = self._ReadDevList()
        env = self._Call(self._environ, {}) or {}
        fromEnv = [part.strip() for part in str(env.get(DEV_ENV, "")).split(";")
                   if part.strip()]
        seen, paths = set(), []
        for path, isEnv in ([(p, False) for p in listed]
                            + [(p, True) for p in fromEnv]):
            folderId = DevFolderId(path)
            if folderId not in seen:
                seen.add(folderId)
                paths.append((os.path.abspath(path), isEnv, isEnv or enabled))
        return paths

    def _ScanDev(self, taken):
        folders, active, plugins, problems = [], set(), {}, {}
        for path, fromEnv, isActive in self._DevPaths():
            folderId = DevFolderId(path)
            if not isActive:
                # Listed while the toggle is off: named, not checked.
                try:
                    raw, _problem = ReadManifest(path)
                except Exception:
                    raw = None
                pluginId = raw.get("id") if isinstance(raw, dict) else None
                folders.append(DevFolder(folderId, path, pluginId
                                         if isinstance(pluginId, str) else None,
                                         fromEnv))
                continue
            active.add(folderId)
            report = self._CheckFolder(path, InstallSource("dev"), dev=True)
            pluginId = (report.manifest.id if report.manifest
                        else os.path.basename(os.path.normpath(path)))
            folders.append(DevFolder(folderId, path,
                                     report.manifest.id if report.manifest else None,
                                     fromEnv))
            if pluginId in taken or pluginId in plugins:
                problems[folderId] = self._Taken(report).problems
                logger.warning("Plugins: a dev folder's id is taken; refused")
                continue
            problems[folderId] = report.problems
            plugins[pluginId] = self._Registered(pluginId, path, Badge.DEV, report)
        return tuple(folders), frozenset(active), plugins, problems

    # ── reading ────────────────────────────────────────────────────────

    @staticmethod
    def _Call(fn, default, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as error:  # an injected part's failure is not ours
            logger.warning(f"Plugins: a callable failed: {type(error).__name__}")
            return default

    def _Live(self, plugin: RegisteredPlugin) -> RegisteredPlugin:
        """``plugin`` with its state, enabled and blocked reason now."""
        report, manifest = plugin.report, plugin.report.manifest
        applies = set()
        builtin, dev = plugin.badge == Badge.BUILTIN, plugin.badge == Badge.DEV
        blocked = None
        if not builtin and not dev:
            blocked = self._Call(self._blockedBy, None, plugin.id,
                                 manifest.version if manifest else None)
            if blocked:
                applies.add(PluginState.BLOCKED)
        with self._lock:
            known = self._describes.get(os.path.normcase(plugin.folder))
        describe = None
        if known and manifest and known[:2] == (manifest.id, manifest.version):
            describe = known[2]
        if not builtin and (not PluginUsable(report) or not UsableJobs(report)
                            or (describe is not None and describe.failure)):
            applies.add(PluginState.INCOMPATIBLE)
        enabled = builtin or bool(self._Call(self._isEnabled, True, plugin.id))
        if not enabled:
            applies.add(PluginState.DISABLED)
        if self._Call(self._isRunning, False, plugin.id):
            applies.add(PluginState.RUNNING)
        if not builtin and manifest is not None and self._Call(
                self._settingsMissing, (), plugin.id, manifest, dev=dev):
            applies.add(PluginState.NEEDS_SETTINGS)
        state = next((s for s in STATE_PRECEDENCE if s in applies),
                     PluginState.READY)
        return dataclasses.replace(plugin, state=state, enabled=enabled,
                                   blocked_reason=blocked, describe=describe)

    def _Snapshot(self):
        with self._lock:
            return list(self._plugins.values())

    def Entries(self) -> Sequence[ListEntry]:
        """The list's rows: the built-in, the installed (by id), the dev
        folders (in their order)."""
        rows = []
        for plugin in map(self._Live, self._Snapshot()):
            manifest = plugin.report.manifest
            rows.append(ListEntry(
                id=plugin.id, name=manifest.name if manifest else plugin.id,
                author=manifest.author if manifest else "",
                version=manifest.version if manifest else "",
                state=plugin.state, badge=plugin.badge,
                size_bytes=plugin.size_bytes, has_icon=plugin.has_icon))
        return rows

    def Get(self, pluginId: str) -> Optional[RegisteredPlugin]:
        with self._lock:
            plugin = self._plugins.get(pluginId)
        return self._Live(plugin) if plugin else None

    def _TriggerJobs(self, matches):
        out = []
        for plugin in map(self._Live, self._Snapshot()):
            if plugin.state in (PluginState.BLOCKED, PluginState.INCOMPATIBLE,
                                PluginState.DISABLED):
                continue
            out.extend((plugin.id, job) for job in UsableJobs(plugin.report)
                       if matches(job))
        return out

    def Actions(self, target: ActionTarget) -> Sequence[Tuple[str, Job]]:
        return self._TriggerJobs(
            lambda job: any(a.target == target for a in job.actions))

    def HookJobs(self, event: HookEvent) -> Sequence[Tuple[str, Job]]:
        return self._TriggerJobs(lambda job: any(h.on == event for h in job.hooks))

    # ── dev folders ────────────────────────────────────────────────────

    def DevFolders(self) -> Sequence[DevFolder]:
        """Every dev folder: dev.json's (also while the toggle is off;
        DevFolderActive says whether it is scanned), then AUTONA_PLUGIN_DEV's."""
        with self._lock:
            return self._devFolders

    def DevFolderActive(self, folderId: str) -> bool:
        """Whether the dev folder is scanned (dev.json's: the toggle is on;
        AUTONA_PLUGIN_DEV's: always)."""
        with self._lock:
            return folderId in self._activeDevIds

    def DevFolderProblems(self, folderId: str) -> Tuple[ManifestProblem, ...]:
        """A dev folder's manifest problems (ID_TAKEN when refused)."""
        with self._lock:
            return self._devProblems.get(folderId, ())

    def RefusedFolders(self) -> Tuple[Tuple[str, Tuple[ManifestProblem, ...]], ...]:
        """(folder, problems) of every folder whose id another plugin owns
        (ID_TAKEN): an installed ``plugins/taguyan.vodsplit/``, a dev folder.
        Not list rows -- a row's id names one plugin -- so the page shows
        them beside the list."""
        with self._lock:
            return self._refused

    def DevMode(self) -> bool:
        return self._ReadDevList()[0]

    def SetDevMode(self, on: bool) -> Optional[Refused]:
        """The developer toggle (dev.json). UNAVAILABLE when dev.json cannot
        be written."""
        try:
            with self._devLock:
                _enabled, folders, _broken = self._ReadDevList()
                self._WriteDevList(on, folders)
        except OSError as error:
            return self._WriteRefused(error)
        self.Scan()
        return None

    @staticmethod
    def _WriteRefused(error):
        logger.warning(f"Plugins: dev.json not written: {type(error).__name__}")
        return Refused(StartRefusal.UNAVAILABLE, detail="dev.json not written")

    def _InstalledFolder(self, path):
        """Whether ``path`` is (in) plugins/ or the built-in's folder, or an
        installed plugin's folder."""
        normal = os.path.normcase(path)
        for root in (self.PluginsRoot(), self.BuiltinDir()):
            root = os.path.normcase(os.path.abspath(root))
            if normal == root or normal.startswith(root + os.sep):
                return True
        with self._lock:
            return any(os.path.normcase(p.folder) == normal
                       for p in self._plugins.values() if p.badge != Badge.DEV)

    def AddDevFolder(self, path: str):
        """Add a dev folder to dev.json. INVALID_REQUEST when it is not a
        folder or is an installed one (plugins/, the built-in's); ID_TAKEN
        when an installed, built-in or other dev plugin has its id;
        UNAVAILABLE when dev.json cannot be written. Adding one listed
        already returns it."""
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            return Refused(StartRefusal.INVALID_REQUEST, detail="not a folder")
        if self._InstalledFolder(path):
            return Refused(StartRefusal.INVALID_REQUEST,
                           detail="an installed plugin's folder")
        folderId = DevFolderId(path)
        raw, problem = ReadManifest(path)
        pluginId = raw.get("id") if problem is None and isinstance(raw, dict) else None
        try:
            with self._devLock:
                enabled, folders, _broken = self._ReadDevList()
                if any(DevFolderId(p) == folderId for p in folders):
                    return next((f for f in self.DevFolders() if f.id == folderId),
                                DevFolder(folderId, path))
                if isinstance(pluginId, str):
                    with self._lock:
                        owner = self._plugins.get(pluginId)
                    if owner is not None and (os.path.normcase(owner.folder)
                                              != os.path.normcase(path)):
                        return Refused(StartRefusal.ID_TAKEN, detail=pluginId)
                self._WriteDevList(enabled, folders + [path])
        except OSError as error:
            return self._WriteRefused(error)
        self.Scan()
        return next((f for f in self.DevFolders() if f.id == folderId),
                    DevFolder(folderId, path,
                              pluginId if isinstance(pluginId, str) else None))

    def RemoveDevFolder(self, folderId: str):
        """Remove dev.json's folder ``folderId`` (the folder stays; an
        AUTONA_PLUGIN_DEV one cannot be removed here): True / False, or
        UNAVAILABLE when dev.json cannot be written."""
        try:
            with self._devLock:
                enabled, folders, _broken = self._ReadDevList()
                kept = [p for p in folders if DevFolderId(p) != folderId]
                if len(kept) == len(folders):
                    return False
                self._WriteDevList(enabled, kept)
        except OSError as error:
            return self._WriteRefused(error)
        self.Scan()
        return True

    def Reload(self, pluginId: str) -> Optional[DescribeResult]:
        """A dev plugin's reload: every manifest again, then its describe.
        None when it is not a dev plugin (any more), its manifest is not
        usable, or no describe runner was given."""
        before = self.Get(pluginId)
        if before is None or before.badge != Badge.DEV:
            return None
        self.Scan()
        plugin = self.Get(pluginId)
        if (plugin is None or plugin.badge != Badge.DEV
                or not PluginUsable(plugin.report) or self._describe is None):
            return None
        manifest = plugin.report.manifest
        run = self._Call(self._describe, None, plugin.folder, manifest.entry,
                         dev=True)
        result = (self.JudgeDescribe(run, manifest) if isinstance(run, DescribeRun)
                  else DescribeResult(None, failure=DescribeFailure.LAUNCH_FAILED))
        with self._lock:
            self._describes[os.path.normcase(plugin.folder)] = (
                manifest.id, manifest.version, result)
        return result

