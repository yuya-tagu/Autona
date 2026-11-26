"""Installing, updating and removing plugins (ADR-009 D7, appendix C; lane
L4). Not wired: nothing imports it yet.

Where things are
----------------
- ``<app>/plugins/<id>/`` -- an installed plugin (the zip's ``<id>/``
  stripped). Its install is staged in ``<id>.new/``, the folder it
  replaces kept in ``<id>.old/`` during the swap, a download written to
  ``<id>.part`` (ZipSafety's suffixes; ``Sweep`` cleans them at launch).
- ``<app>/plugins/.upload-<16 hex>.part`` -- a manual zip's private copy
  while it is prepared.
- ``<user_data>/plugins/<id>/install.json`` -- the install record:
  ``{"source": {"kind", "repo_id"?, "name"?}, "version", "sha256",
  "consent_digest", "installed_at"}`` (UTF-8, no BOM; written through a
  fsynced temporary file inside the swap, so a record that cannot be
  written puts the old folder back).

An install (C.3) is two calls around the consent screen
-------------------------------------------------------
``PrepareFromListing(repoId, listedId)`` / ``PrepareFromZip(zipPath)``:
download (listing) -> the zip's size / SHA-256 against the release
descriptor -> ``CheckZip`` -> the source -> ``DecideSource`` (a refusal
raises) -> the blocklist -> unpack to ``<id>.new`` -> read plugin.json and
``registry.Check`` it there -> a ``PendingInstall`` (what the consent
screen shows, ``needs_consent``). Nothing of the plugin is started.

``Commit(pending)`` after consent (or the confirmation): not while the
plugin runs (``jobs.Running``, asked again just before the swap), not when
a dev folder took the id, the blocklist again, ``describe`` in the
``.new`` folder (``runner.Describe`` then ``registry.JudgeDescribe``);
for a NEW_INSTALL the hooks disabled and user_data/plugins/<id>/ emptied
but install.json (secrets.bin, config.json, hooks.json and, when another
source was recorded, data/: another source's zip inherits nothing, D7),
for a changed digest the hooks
disabled (RECONSENT, D.6); then the swap with install.json written inside
it. ``Discard(pending)`` drops it (the consent refused). One install per
id at a time (IN_PROGRESS); the free space is asked before a download, a
copy and an unpacking (DISK_FULL).

A manual zip's source is ``manual`` unless its SHA-256 equals the
``zip.sha256`` of the latest release descriptor of the repository recorded
for the installed id, or -- none recorded -- of plugin.json's
``update.github`` (its numeric id looked up once): then it is that
repository's (``PendingInstall.verified``). Offline, it stays manual.

The network (``ReleaseNetwork``), describe (``runner``), the manifest's
check and describe's judgement (``registry``), the folders' roots, the
clock and the sleep of the rename's retries are given: this module opens
no connection and starts no process of its own.

Pure parts: ``CheckDescriptor`` / ``ParseDescriptor`` (autona-plugin.json),
``ConsentDigest``, ``ParseBlocklist`` / ``BlockedBy`` (plugins.json's
``blocked``: by id and versions, by the installed zip's SHA-256, by the
recorded repo_id; a dev folder has none of these and is never passed),
``DecideSource`` (the C.3 table).

``Remove`` (C.6) and ``Sweep`` (E.2) are the rest of the Protocol.

Logs: the plugin id and the outcome, never a path or a value (E.3).

Threads: every call blocks its caller (a TaskExecutor job); one lock
guards the pending / busy tables only. Nothing is made, read or written
on import (pitfalls #1); no Qt.
"""

import hashlib
import json
import os
import re
import shutil
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from typing import (Any, Callable, Iterable, List, Mapping, Optional,
                    Protocol, Sequence, Tuple)

from loguru import logger

from . import ZipSafety
from .PluginSettings import PLUGINS_DIR, UserDir
from .PluginTypes import (MANIFEST_FILE, MANIFEST_MAX_BYTES,
                          PLUGIN_ID_PATTERN, RELEASE_DESCRIPTOR_MAX_BYTES,
                          RESERVED_ENTRY_PREFIX, RESERVED_ID_PREFIX,
                          VERSION_PATTERN, ZIP_EXPANDED_MAX_BYTES,
                          ZIP_MAX_BYTES, ZIP_MAX_ENTRIES,
                          INSTALLED_PATH_MAX_LENGTH, BlockEntry,
                          FieldType, HookDisabledReason, InstallDecision,
                          InstallRecord, InstallSource, Manifest,
                          ManifestCheck, ManifestReport, ProblemScope,
                          SOURCE_FIELD_TYPES)

INSTALL_FILE = "install.json"
SECRETS_FILE = "secrets.bin"
DATA_DIR = "data"
OUT_DIR = "out"

# install.json source kinds (PluginTypes.InstallSource.kind).
SOURCE_GITHUB = "github"
SOURCE_MANUAL = "manual"

# The official repositories' numeric ids: the only sources a taguyan. id
# installs from (ADR-009 D7). Empty until the first official plugin's
# repository exists (taguyan.webhook, after M2).
OFFICIAL_REPO_IDS: frozenset = frozenset()

# The built-in plugins: never removed by the installer (ADR-009 D9).
BUILTIN_IDS: frozenset = frozenset({"taguyan.vodsplit"})

# What a download, a copy or an unpacking leaves free on the drive.
DISK_MARGIN_BYTES = 64 * 1024 * 1024

# Remove's ``runs``.
RUNS_DELETE = "delete"
RUNS_KEEP_OUT = "keep_out"
RUNS_KEEP = "keep"
_RUNS = (RUNS_DELETE, RUNS_KEEP_OUT, RUNS_KEEP)

# CheckZip's codes beyond ZipSafety's (the installer's own: the Protocol
# leaves them to it).
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

# InstallError's codes.
NETWORK_FAILED = "network_failed"
BAD_ZIP = "bad_zip"
MANIFEST_INVALID = "manifest_invalid"
BLOCKED = "blocked"
IN_PROGRESS = "in_progress"
RUNNING = "running"
DESCRIBE_FAILED = "describe_failed"
EXTRACT_FAILED = "extract_failed"
SWAP_FAILED = "swap_failed"
NOT_PENDING = "not_pending"
NOT_FOUND = "not_found"
REMOVE_FAILED = "remove_failed"
SECRETS_FAILED = "secrets_failed"
STOPPED = "stopped"
INVALID_REQUEST = "invalid_request"
DISK_FULL = "disk_full"
BUILTIN = "builtin"

_PLUGIN_ID = re.compile(PLUGIN_ID_PATTERN)
_VERSION = re.compile(VERSION_PATTERN)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GITHUB_REPO = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
_LEFTOVER = re.compile(
    r"^(" + PLUGIN_ID_PATTERN.strip("^$") + r")(\.new|\.old|\.part)$")
_UPLOAD_PREFIX = ".upload-"
_UPLOAD = re.compile(r"^\.upload-[0-9a-f]{16}\.part$")
_COMPARISON = re.compile(r"^(<=|>=|<|>|=)\s*(\S+)$")
_DIGEST_VERSION = 1
_CHUNK = 1024 * 1024


class InstallError(Exception):
    """An install / removal not carried out: ``code`` (this module's),
    ``problems`` (CheckZip's codes, ManifestProblem codes, a
    DescribeFailure) and a ``detail`` without paths or values."""

    def __init__(self, code: str, problems: Sequence[str] = (),
                 detail: str = ""):
        super().__init__(code if not problems
                         else f"{code}: {', '.join(problems)}")
        self.code = code
        self.problems = tuple(problems)
        self.detail = detail


class ReleaseNetwork(Protocol):
    """GitHub, as C.3 reads it (the wiring's; never called with a lock
    held). Any exception is a network failure."""

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
        to ``target`` (resumed when it is there), ``size`` bytes."""


@dataclass(frozen=True)
class PendingInstall:
    """A prepared install waiting for consent: what the consent screen
    shows (C.3 step 5) and what Commit needs. ``size_bytes`` is the
    unpacked size; ``zip_bytes`` the zip's."""
    plugin_id: str
    version: str
    manifest: Manifest
    report: ManifestReport
    source: InstallSource
    verified: bool
    sha256: str
    zip_bytes: int
    size_bytes: int
    decision: InstallDecision
    consent_digest: str
    needs_consent: bool
    installed: Optional[InstallRecord]
    new_dir: str


# ── pure parts ──────────────────────────────────────────────────────────

def VersionTuple(text: Any) -> Optional[Tuple[int, int, int]]:
    """A strict ``X.Y.Z`` as a tuple, else None."""
    if not isinstance(text, str) or not _VERSION.fullmatch(text):
        return None
    return tuple(int(part) for part in text.split("."))


def FileSha256(path: str) -> str:
    """The SHA-256 of a file (lower-case hex). Raises OSError."""
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        while True:
            chunk = file.read(_CHUNK)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


def _UrlPart(text: Any) -> bool:
    """A tag / zip name that can go into a URL path (C.1): no ``/``,
    ``\\``, ``..``, ``%``, control character, and none of ``?``, ``#`` or
    white space either (they would end or break the path)."""
    return (isinstance(text, str) and bool(text) and len(text) <= 200
            and not any(ch in "/\\%?#" or ord(ch) < 32 or ord(ch) == 127
                        or ch.isspace() for ch in text)
            and ".." not in text)


def _RepoName(name: Any) -> bool:
    """``owner/repo`` as GitHub names them; a repo part ``.`` / ``..`` is
    refused (it would climb in a URL)."""
    return (isinstance(name, str) and bool(_GITHUB_REPO.fullmatch(name))
            and name.split("/", 1)[1] not in (".", ".."))


def _Int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def ParseDescriptor(content: bytes) -> Optional[Mapping[str, Any]]:
    """autona-plugin.json's object, or None (over
    RELEASE_DESCRIPTOR_MAX_BYTES, not UTF-8 JSON, not an object)."""
    if not isinstance(content, (bytes, bytearray)) \
            or len(content) > RELEASE_DESCRIPTOR_MAX_BYTES:
        return None
    try:
        data = json.loads(bytes(content).decode("utf-8-sig"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def CheckDescriptor(data: Any) -> List[str]:
    """[DESCRIPTOR_INVALID] when autona-plugin.json (C.1) does not fit,
    else []."""
    if not isinstance(data, Mapping):
        return [DESCRIPTOR_INVALID]
    zipInfo = data.get("zip")
    protocol = data.get("protocol")
    fits = (
        data.get("autona_plugin") == 1 and _Int(data.get("autona_plugin"))
        and isinstance(data.get("id"), str)
        and bool(_PLUGIN_ID.fullmatch(data["id"]))
        and VersionTuple(data.get("version")) is not None
        and _UrlPart(data.get("tag"))
        and isinstance(protocol, list) and bool(protocol)
        and all(_Int(item) for item in protocol)
        and VersionTuple(data.get("min_autona")) is not None
        and isinstance(zipInfo, Mapping)
        and _UrlPart(zipInfo.get("name"))
        and _Int(zipInfo.get("size"))
        and 0 < zipInfo["size"] <= ZIP_MAX_BYTES
        and isinstance(zipInfo.get("sha256"), str)
        and bool(_SHA256.fullmatch(zipInfo["sha256"])))
    return [] if fits else [DESCRIPTOR_INVALID]


def _CanonicalText(text: Any) -> Any:
    if isinstance(text, Mapping):
        return {str(key): str(value) for key, value in text.items()}
    return text if isinstance(text, str) else None


def ConsentDigest(manifest: Manifest, source: InstallSource) -> str:
    """One SHA-256 of what consent shows (D7): the declarations
    (``permissions``, hosts case-folded and as a set), the source types the
    jobs take, the secret settings' names, the hook keys and the source
    (kind and repo_id: a rename is followed silently). Independent of the
    manifest's order. Pure."""
    permissions = manifest.permissions
    sources = sorted({field.type for job in manifest.jobs
                      for field in job.params
                      if field.type in SOURCE_FIELD_TYPES})
    secrets = sorted({field.key for field in manifest.settings
                      if field.type == FieldType.SECRET})
    hooks = sorted({f"{job.id}/{hook.on}" for job in manifest.jobs
                    for hook in job.hooks})
    payload = {
        "digest": _DIGEST_VERSION,
        "permissions": {
            "network": sorted({host.lower() for host in permissions.network}),
            "files": permissions.files,
            "spawn": bool(permissions.spawn),
            "autona_api": permissions.autona_api,
            "note": _CanonicalText(permissions.note),
        },
        "sources": [str(kind) for kind in sources],
        "secrets": secrets,
        "hooks": hooks,
        "source": {"kind": source.kind,
                   "repo_id": source.repo_id
                   if source.kind == SOURCE_GITHUB else None},
    }
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ParseBlocklist(listing: Any) -> Tuple[BlockEntry, ...]:
    """plugins.json's ``blocked`` rows (C.5) as BlockEntry. A row is kept
    when it names at least one of: an ``id`` (with ``versions``; missing is
    ``*``), a ``sha256``, a ``repo_id``. What does not fit in a kept row is
    read so that it blocks more, never less: a ``versions`` that cannot be
    read is ``*``. A row naming none of the three is dropped (it cannot
    match anything)."""
    rows = listing.get("blocked") if isinstance(listing, Mapping) else None
    if not isinstance(rows, list):
        return ()
    entries = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        pluginId = row.get("id")
        if not (isinstance(pluginId, str) and _PLUGIN_ID.fullmatch(pluginId)):
            pluginId = None
        sha = row.get("sha256")
        sha = sha.lower() if isinstance(sha, str) \
            and _SHA256.fullmatch(sha.lower()) else None
        repoId = row.get("repo_id")
        repoId = repoId if _Int(repoId) and repoId > 0 else None
        if pluginId is None and sha is None and repoId is None:
            continue
        versions = None
        if pluginId is not None:
            versions = row.get("versions")
            if not (isinstance(versions, str) and _VersionsReadable(versions)):
                versions = "*"
        reason = _CanonicalText(row.get("reason"))
        since = row.get("since")
        entries.append(BlockEntry(
            reason=reason if reason is not None else "",
            since=since if isinstance(since, str) else "",
            id=pluginId, versions=versions, sha256=sha, repo_id=repoId))
    return tuple(entries)


def _VersionsReadable(versions: str) -> bool:
    if versions.strip() == "*":
        return True
    match = _COMPARISON.fullmatch(versions.strip())
    return bool(match) and VersionTuple(match.group(2)) is not None


def VersionMatches(versions: Optional[str], version: str) -> bool:
    """Whether ``version`` is in ``versions`` (``*`` or one comparison and
    X.Y.Z). One that cannot be compared matches (a block errs on the
    blocking side)."""
    if versions is None or versions.strip() == "*":
        return True
    match = _COMPARISON.fullmatch(versions.strip())
    left, right = VersionTuple(version), \
        VersionTuple(match.group(2)) if match else None
    if not match or left is None or right is None:
        return True
    return {"<": left < right, "<=": left <= right, "=": left == right,
            ">=": left >= right, ">": left > right}[match.group(1)]


def BlockedBy(pluginId: str, version: str, sha256: Optional[str],
              repoId: Optional[int],
              blocklist: Sequence[BlockEntry]) -> Optional[BlockEntry]:
    """The first row ``pluginId`` matches: by its id and versions, by the
    installed zip's SHA-256, by the recorded repo_id (any of a row's keys
    matching is a match). Pure."""
    sha = sha256.lower() if isinstance(sha256, str) else None
    for entry in blocklist:
        if entry.id is not None and entry.id == pluginId \
                and VersionMatches(entry.versions, version):
            return entry
        if entry.sha256 is not None and sha is not None \
                and entry.sha256.lower() == sha:
            return entry
        if entry.repo_id is not None and repoId is not None \
                and entry.repo_id == repoId:
            return entry
    return None


def DecideSource(pluginId: str, installed: Optional[InstallRecord],
                 incoming: InstallSource, incomingVersion: str, *,
                 devIdExists: bool,
                 officialRepoIds: Iterable[int] = OFFICIAL_REPO_IDS
                 ) -> InstallDecision:
    """The C.3 table. A dev folder with the id refuses; a taguyan. id from
    anything but an official repository id refuses; the same recorded
    repo_id is an update (a higher version) or a confirmed reinstall (the
    same or lower, or a version that cannot be compared); anything else
    -- nothing recorded, another repo_id, manual to manual, manual to
    github -- is a new install. Pure."""
    if devIdExists:
        return InstallDecision.REFUSE_DEV_ID
    if pluginId.startswith(RESERVED_ID_PREFIX) and not (
            incoming.kind == SOURCE_GITHUB
            and incoming.repo_id in frozenset(officialRepoIds)):
        return InstallDecision.REFUSE_RESERVED
    if installed is not None and installed.source.kind == SOURCE_GITHUB \
            and incoming.kind == SOURCE_GITHUB \
            and installed.source.repo_id is not None \
            and installed.source.repo_id == incoming.repo_id:
        left, right = VersionTuple(incomingVersion), \
            VersionTuple(installed.version)
        if left is not None and right is not None and left > right:
            return InstallDecision.UPDATE
        return InstallDecision.CONFIRM_SAME_OR_OLDER
    return InstallDecision.NEW_INSTALL


# ── the installer ───────────────────────────────────────────────────────

class PluginInstaller:
    """``appDir``: the Autona folder (``plugins/`` beside Autona.exe).
    ``userDataDir``: user_data. ``registry``: PluginRegistry's ``Check`` and
    ``JudgeDescribe``. ``runner``: PluginRunner's ``Describe``.
    ``hostVersion``: this Autona's version (Check's min_autona).
    ``network``: ReleaseNetwork (None: listing installs fail, manual zips
    stay manual). ``secrets`` / ``hooks`` / ``jobs``: PluginSecrets
    (``Forget``), PluginHooks (``Forget``), PluginJobs (``Running``), each
    optional. ``workDir()``: archive.work_dir now (the runs' folders).
    ``isDevId(id)``: whether a dev folder has the id. ``blocklist()``: the
    blocked rows now (ParseBlocklist's). ``stopping()``: Autona is ending
    (asked while unpacking). ``clock`` / ``sleep``: time.time / time.sleep."""

    def __init__(self, appDir: str, userDataDir: str, *, registry, runner,
                 hostVersion: str,
                 network: Optional[ReleaseNetwork] = None,
                 secrets=None, hooks=None, jobs=None,
                 workDir: Optional[Callable[[], Optional[str]]] = None,
                 isDevId: Optional[Callable[[str], bool]] = None,
                 blocklist: Optional[Callable[[], Sequence[BlockEntry]]] = None,
                 stopping: Optional[Callable[[], bool]] = None,
                 officialRepoIds: Iterable[int] = OFFICIAL_REPO_IDS,
                 clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep):
        self.pluginsDir = os.path.join(os.path.abspath(appDir), PLUGINS_DIR)
        self.userDataDir = os.path.abspath(userDataDir)
        self._registry = registry
        self._runner = runner
        self._hostVersion = hostVersion
        self._network = network
        self._secrets = secrets
        self._hooks = hooks
        self._jobs = jobs
        self._workDir = workDir or (lambda: None)
        self._isDevId = isDevId or (lambda _pluginId: False)
        self._blocklist = blocklist or (lambda: ())
        self._stopping = stopping or (lambda: False)
        self._officialRepoIds = frozenset(officialRepoIds)
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._busy: set = set()
        self._pending: dict = {}

    # ── paths and the record ───────────────────────────────────────────

    def PluginDir(self, pluginId: str) -> str:
        _RequireId(pluginId)
        return os.path.join(self.pluginsDir, pluginId)

    def InstallPath(self, pluginId: str) -> str:
        return os.path.join(UserDir(self.userDataDir, pluginId, dev=False),
                            INSTALL_FILE)

    def ReadInstallRecord(self, pluginId: str) -> Optional[InstallRecord]:
        """install.json, or None when missing or damaged (a damaged record
        makes the next install a new one: the safe side)."""
        try:
            with open(self.InstallPath(pluginId), "rb") as file:
                data = json.loads(file.read().decode("utf-8-sig"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        source = data.get("source")
        if not isinstance(source, dict):
            return None
        kind, repoId, name = (source.get("kind"), source.get("repo_id"),
                              source.get("name"))
        if kind == SOURCE_GITHUB:
            if not (_Int(repoId) and repoId > 0):
                return None
        elif kind == SOURCE_MANUAL:
            repoId = None
        else:
            return None
        sha = data.get("sha256")
        digest = data.get("consent_digest")
        if VersionTuple(data.get("version")) is None \
                or not (sha is None or (isinstance(sha, str)
                                        and _SHA256.fullmatch(sha))) \
                or not isinstance(digest, str):
            return None
        return InstallRecord(
            source=InstallSource(kind=kind, repo_id=repoId,
                                 name=name if isinstance(name, str) else None),
            version=data["version"], sha256=sha, consent_digest=digest)

    def _WriteRecord(self, pluginId: str, record: InstallRecord) -> None:
        """install.json through ``.tmp``: written, flushed, fsynced, then
        replaced (retried while an antivirus holds it); the ``.tmp`` is
        removed when any step fails. Raises OSError."""
        path = self.InstallPath(pluginId)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        source = {"kind": record.source.kind}
        if record.source.kind == SOURCE_GITHUB:
            source["repo_id"] = record.source.repo_id
            source["name"] = record.source.name
        data = {"source": source, "version": record.version,
                "sha256": record.sha256,
                "consent_digest": record.consent_digest,
                "installed_at": datetime.fromtimestamp(self._clock())
                .astimezone().isoformat(timespec="seconds")}
        temporary = path + ".tmp"
        try:
            with open(temporary, "w", encoding="utf-8", newline="\n") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            ZipSafety.Replace(temporary, path, sleep=self._sleep)
        except BaseException:
            self._RemoveFile(temporary)
            raise

    # ── the Protocol's pure parts ──────────────────────────────────────

    def DecideSource(self, pluginId: str,
                     installed: Optional[InstallRecord],
                     incoming: InstallSource, incomingVersion: str, *,
                     devIdExists: bool) -> InstallDecision:
        return DecideSource(pluginId, installed, incoming, incomingVersion,
                            devIdExists=devIdExists,
                            officialRepoIds=self._officialRepoIds)

    def ConsentDigest(self, manifest: Manifest,
                      source: InstallSource) -> str:
        return ConsentDigest(manifest, source)

    def BlockedBy(self, pluginId: str, version: str, sha256: Optional[str],
                  repoId: Optional[int],
                  blocklist: Sequence[BlockEntry]) -> Optional[BlockEntry]:
        return BlockedBy(pluginId, version, sha256, repoId, blocklist)

    def BlockedInstalled(self, versions: Mapping[str, str],
                         blocklist: Sequence[BlockEntry]
                         ) -> dict:
        """Installed plugin id -> the row it matches, for ``versions`` (id
        -> its manifest's version: the installed ones only, never a dev or
        the built-in), each with its install.json's SHA-256 and repo_id."""
        blocked = {}
        for pluginId, version in versions.items():
            record = self.ReadInstallRecord(pluginId)
            entry = BlockedBy(
                pluginId, version, record.sha256 if record else None,
                record.source.repo_id if record else None, blocklist)
            if entry is not None:
                blocked[pluginId] = entry
        return blocked

    # ── the zip ────────────────────────────────────────────────────────

    def CheckZip(self, zipPath: str,
                 descriptor: Optional[Mapping[str, Any]]) -> Sequence[str]:
        """The zip's problems (C.1 / C.7; empty when fine), against the
        release descriptor when given (its own fields, the zip's size and
        SHA-256, plugin.json's id and version). Never raises for a zip it
        cannot read: that is ZIP_UNREADABLE."""
        return self._Inspect(zipPath, descriptor)[0]

    def _Inspect(self, zipPath: str, descriptor: Optional[Mapping[str, Any]],
                 sha: Optional[str] = None):
        """(problems, plan, the plugin id, plugin.json's object)."""
        problems: List[str] = []

        def add(*codes):
            for code in codes:
                if code not in problems:
                    problems.append(code)

        unreadable = [ZipSafety.ZIP_UNREADABLE], None, None, None
        try:
            size = os.path.getsize(zipPath)
        except OSError:
            return unreadable
        if size > ZIP_MAX_BYTES:
            return [ZIP_TOO_LARGE], None, None, None
        if descriptor is not None:
            add(*CheckDescriptor(descriptor))
            if not problems:
                if size != descriptor["zip"]["size"]:
                    add(SIZE_MISMATCH)
                try:
                    actual = sha or FileSha256(zipPath)
                except OSError:
                    return unreadable
                if actual != descriptor["zip"]["sha256"]:
                    add(SHA256_MISMATCH)
        refused = ZipSafety.CheckCentralDirectory(zipPath,
                                                  maxEntries=ZIP_MAX_ENTRIES)
        if refused is not None:
            add(refused)
            return problems, None, None, None
        try:
            archive = zipfile.ZipFile(zipPath)
        except (OSError,) + ZipSafety.READ_ERRORS:
            add(ZipSafety.ZIP_UNREADABLE)
            return problems, None, None, None
        with archive:
            plan = ZipSafety.Inspect(archive, maxEntries=ZIP_MAX_ENTRIES,
                                     maxExpandedBytes=ZIP_EXPANDED_MAX_BYTES)
            add(*plan.problems)
            if ZipSafety.TOO_MANY_ENTRIES in plan.problems:
                return problems, plan, None, None
            tops = {entry.parts[0] for entry in plan.entries}
            if len(tops) != 1 or any(len(entry.parts) == 1
                                     and not entry.is_dir
                                     for entry in plan.entries):
                add(NOT_ONE_FOLDER)
                return problems, plan, None, None
            pluginId = next(iter(tops))
            if not _PLUGIN_ID.fullmatch(pluginId):
                add(FOLDER_NOT_ID)
                return problems, plan, None, None
            raw = self._ReadZipManifest(archive, plan, pluginId, add)
            if raw is not None:
                if raw.get("id") != pluginId:
                    add(ID_MISMATCH)
                if descriptor is not None and not CheckDescriptor(descriptor):
                    if descriptor["id"] != pluginId:
                        add(ID_MISMATCH)
                    if descriptor["version"] != raw.get("version"):
                        add(VERSION_MISMATCH)
                self._CheckEntry(raw.get("entry"), plan, pluginId, add)
            final = os.path.join(self.pluginsDir, pluginId)
            for root in (final, final + ZipSafety.NEW_SUFFIX):
                longestFile, longestFolder = ZipSafety.PathLengths(
                    root, plan.entries, strip=1)
                if longestFile > INSTALLED_PATH_MAX_LENGTH \
                        or longestFolder > ZipSafety.MAX_FOLDER_PATH:
                    add(ZipSafety.PATH_TOO_LONG)
            return problems, plan, pluginId, raw

    @staticmethod
    def _ReadZipManifest(archive, plan, pluginId, add):
        """``<id>/plugin.json``'s object (utf-8-sig, at most
        MANIFEST_MAX_BYTES), or None (MANIFEST_MISSING /
        MANIFEST_UNREADABLE added: a damaged or unsupported entry too)."""
        for entry in plan.entries:
            if entry.parts == (pluginId, MANIFEST_FILE) and not entry.is_dir:
                break
        else:
            add(MANIFEST_MISSING)
            return None
        if entry.info.file_size > MANIFEST_MAX_BYTES:
            add(MANIFEST_UNREADABLE)
            return None
        try:
            with archive.open(entry.info) as file:
                content = file.read(MANIFEST_MAX_BYTES + 1)
            data = json.loads(content.decode("utf-8-sig"))
        except (OSError,) + ZipSafety.READ_ERRORS:
            add(MANIFEST_UNREADABLE)
            return None
        if not isinstance(data, dict):
            add(MANIFEST_UNREADABLE)
            return None
        return data

    @staticmethod
    def _CheckEntry(entry, plan, pluginId, add):
        """``entry[0]`` a file of the zip under ``<id>/`` (C.1). A malformed
        or reserved entry is left to the manifest's check."""
        if not isinstance(entry, list) or not entry \
                or not isinstance(entry[0], str) \
                or entry[0].startswith(RESERVED_ENTRY_PREFIX):
            return
        parts, problem = ZipSafety.NameProblem(entry[0], asciiOnly=False)
        if problem is not None:
            add(ENTRY_OUTSIDE)
            return
        wanted = tuple(part.casefold() for part in (pluginId, *parts))
        if not any(tuple(part.casefold() for part in item.parts) == wanted
                   and not item.is_dir for item in plan.entries):
            add(ENTRY_MISSING)

    # ── an install ─────────────────────────────────────────────────────

    def _Reserve(self, pluginId: str) -> None:
        with self._lock:
            if pluginId in self._busy:
                raise InstallError(IN_PROGRESS)
            self._busy.add(pluginId)
            stale = self._pending.pop(pluginId, None)
        if stale is not None:
            self._RemoveNew(stale.new_dir)

    def _Release(self, pluginId: str) -> None:
        with self._lock:
            self._busy.discard(pluginId)

    def _RemoveNew(self, newDir: str) -> None:
        try:
            if os.path.isdir(newDir):
                ZipSafety.RemoveTree(newDir)
        except OSError as error:
            logger.warning(f"Plugin install: a staged folder could not be "
                           f"removed ({ZipSafety.Why(error)}); removed at "
                           f"the next start")

    def _RequireSpace(self, needed: int) -> None:
        """DISK_FULL when the plugins folder's drive has not ``needed``
        bytes and DISK_MARGIN_BYTES free (a stream's recording shares the
        drive). Unknown free space is not a refusal."""
        try:
            free = shutil.disk_usage(self.pluginsDir).free
        except OSError:
            return
        if free < needed + DISK_MARGIN_BYTES:
            raise InstallError(DISK_FULL)

    def PrepareFromListing(self, repoId: int,
                           listedId: str) -> PendingInstall:
        """C.3 steps 1-4 for a listed plugin (``repoId`` and the listing's
        id). A failed download keeps ``<id>.part`` for the next try (Sweep
        removes it at the next launch). Raises InstallError."""
        _RequireId(listedId)
        if self._network is None or not (_Int(repoId) and repoId > 0):
            raise InstallError(NETWORK_FAILED if self._network is None
                               else INVALID_REQUEST)
        try:
            name = self._network.RepoName(repoId)
            if not _RepoName(name):
                raise ValueError("not a repository name")
            descriptor = ParseDescriptor(self._network.LatestDescriptor(name))
        except Exception as error:
            raise InstallError(NETWORK_FAILED,
                               detail=type(error).__name__) from None
        if descriptor is None or CheckDescriptor(descriptor):
            raise InstallError(BAD_ZIP, (DESCRIPTOR_INVALID,))
        if descriptor["id"] != listedId:
            raise InstallError(BAD_ZIP, (ID_MISMATCH,))
        source = InstallSource(kind=SOURCE_GITHUB, repo_id=repoId, name=name)
        self._Reserve(listedId)
        part = os.path.join(self.pluginsDir,
                            listedId + ZipSafety.PART_SUFFIX)
        keepPart = False
        try:
            # Read under the reservation: no other install of the id ends
            # between the record's read and the decision.
            installed = self.ReadInstallRecord(listedId)
            self._Refusals(listedId, descriptor["version"], None, source,
                           installed)
            os.makedirs(self.pluginsDir, exist_ok=True)
            self._RequireSpace(descriptor["zip"]["size"])
            try:
                self._network.Download(name, descriptor["tag"],
                                       descriptor["zip"]["name"], part,
                                       descriptor["zip"]["size"])
            except Exception as error:
                keepPart = True
                raise InstallError(NETWORK_FAILED,
                                   detail=type(error).__name__) from None
            return self._Stage(part, descriptor, source, verified=True,
                               installed=installed)
        except BaseException:
            self._Release(listedId)
            raise
        finally:
            if not keepPart:
                self._RemoveFile(part)

    def PrepareFromZip(self, zipPath: str) -> PendingInstall:
        """C.3 steps 4- for a zip the user gave. It is first copied into a
        private file in the plugins folder (its SHA-256 computed on the
        way), and everything after reads that copy: the zip cannot change
        between its hash and its unpacking. The user's file stays. Raises
        InstallError."""
        try:
            size = os.path.getsize(zipPath)
        except OSError:
            raise InstallError(BAD_ZIP, (ZipSafety.ZIP_UNREADABLE,)) from None
        if size > ZIP_MAX_BYTES:
            raise InstallError(BAD_ZIP, (ZIP_TOO_LARGE,))
        try:
            os.makedirs(self.pluginsDir, exist_ok=True)
        except OSError as error:
            raise InstallError(EXTRACT_FAILED,
                               detail=ZipSafety.Why(error)) from None
        self._RequireSpace(size)
        copy = os.path.join(self.pluginsDir,
                            f"{_UPLOAD_PREFIX}{os.urandom(8).hex()}"
                            f"{ZipSafety.PART_SUFFIX}")
        try:
            try:
                sha = _CopyHashing(zipPath, copy, size)
            except OSError:
                raise InstallError(BAD_ZIP,
                                   (ZipSafety.ZIP_UNREADABLE,)) from None
            problems, _plan, pluginId, raw = self._Inspect(copy, None, sha)
            if problems:
                raise InstallError(BAD_ZIP, problems)
            self._Reserve(pluginId)
            try:
                installed = self.ReadInstallRecord(pluginId)
                source, descriptor = self._MatchRelease(sha, pluginId, raw,
                                                        installed)
                self._Refusals(pluginId, raw.get("version"), sha, source,
                               installed)
                return self._Stage(copy, descriptor, source,
                                   verified=source.kind == SOURCE_GITHUB,
                                   installed=installed, sha=sha)
            except BaseException:
                self._Release(pluginId)
                raise
        finally:
            self._RemoveFile(copy)

    def _MatchRelease(self, sha, pluginId, raw, installed):
        """(source, descriptor) of a manual zip: the repository recorded
        for the id, else plugin.json's update.github, when its latest
        release's zip.sha256 is ``sha``; else (manual, None)."""
        manual = (InstallSource(kind=SOURCE_MANUAL), None)
        if self._network is None:
            return manual
        try:
            if installed is not None and installed.source.kind == SOURCE_GITHUB:
                repoId = installed.source.repo_id
                name = self._network.RepoName(repoId)
            else:
                update = raw.get("update")
                name = update.get("github") if isinstance(update, dict) \
                    else None
                if not _RepoName(name):
                    return manual
                repoId = self._network.RepoId(name)
            if not (_Int(repoId) and repoId > 0 and _RepoName(name)):
                return manual
            descriptor = ParseDescriptor(self._network.LatestDescriptor(name))
        except Exception as error:
            logger.info(f"Plugin install: {pluginId}: the release could not "
                        f"be read ({type(error).__name__}); the zip is "
                        f"manual")
            return manual
        if descriptor is None or CheckDescriptor(descriptor) \
                or descriptor["zip"]["sha256"] != sha \
                or descriptor["id"] != pluginId \
                or descriptor["version"] != raw.get("version"):
            return manual
        return InstallSource(kind=SOURCE_GITHUB, repo_id=repoId,
                             name=name), descriptor

    def _Refusals(self, pluginId, version, sha, source, installed):
        """DecideSource's refusals and the blocklist, before anything is
        written. Returns the decision."""
        version = version if isinstance(version, str) else ""
        decision = self.DecideSource(pluginId, installed, source, version,
                                     devIdExists=self._isDevId(pluginId))
        if decision in (InstallDecision.REFUSE_DEV_ID,
                        InstallDecision.REFUSE_RESERVED):
            raise InstallError(decision.value)
        if BlockedBy(pluginId, version, sha, source.repo_id,
                     self._blocklist()) is not None:
            raise InstallError(BLOCKED)
        return decision

    def _Stage(self, zipPath, descriptor, source, *, verified, installed,
               sha=None) -> PendingInstall:
        """Check the zip (against ``descriptor``), unpack it to
        ``<id>.new`` and check its manifest there. The id is reserved; the
        staged folder is removed whatever fails after it was made."""
        try:
            zipBytes = os.path.getsize(zipPath)
            sha = sha or FileSha256(zipPath)
        except OSError:
            raise InstallError(BAD_ZIP, (ZipSafety.ZIP_UNREADABLE,)) from None
        problems, plan, pluginId, raw = self._Inspect(zipPath, descriptor, sha)
        if problems:
            raise InstallError(BAD_ZIP, problems)
        decision = self._Refusals(pluginId, raw.get("version"), sha, source,
                                  installed)
        self._RequireSpace(plan.expanded_bytes)
        newDir = os.path.join(self.pluginsDir,
                              pluginId + ZipSafety.NEW_SUFFIX)
        self._RemoveNew(newDir)
        if os.path.lexists(newDir):
            raise InstallError(EXTRACT_FAILED)
        try:
            try:
                with zipfile.ZipFile(zipPath) as archive:
                    ZipSafety.Extract(archive, plan.entries, newDir, strip=1,
                                      step=self._stopping)
            except ZipSafety.ZipStopped:
                raise InstallError(STOPPED) from None
            except (OSError,) + ZipSafety.READ_ERRORS as error:
                raise InstallError(EXTRACT_FAILED,
                                   detail=ZipSafety.Why(error)) from None
            report = self._CheckManifest(newDir, pluginId, source)
            manifest = report.manifest
            digest = ConsentDigest(manifest, source)
            pending = PendingInstall(
                plugin_id=pluginId, version=manifest.version,
                manifest=manifest, report=report, source=source,
                verified=verified, sha256=sha, zip_bytes=zipBytes,
                size_bytes=plan.expanded_bytes, decision=decision,
                consent_digest=digest,
                needs_consent=(decision == InstallDecision.NEW_INSTALL
                               or installed is None
                               or installed.consent_digest != digest),
                installed=installed, new_dir=newDir)
        except BaseException:
            self._RemoveNew(newDir)
            raise
        with self._lock:
            self._pending[pluginId] = pending
            self._busy.discard(pluginId)
        logger.info(f"Plugin install: {pluginId} {manifest.version} is "
                    f"staged ({decision.value})")
        return pending

    def _CheckManifest(self, newDir, pluginId, source) -> ManifestReport:
        """registry.Check of the staged plugin.json. MANIFEST_INVALID when
        it cannot be read or has a plugin-scope problem."""
        path = os.path.join(newDir, MANIFEST_FILE)
        try:
            with open(path, "rb") as file:
                content = file.read(MANIFEST_MAX_BYTES + 1)
            raw = json.loads(content.decode("utf-8-sig"))
        except (OSError, ValueError):
            raise InstallError(MANIFEST_INVALID,
                               (ManifestCheck.MANIFEST_UNREADABLE.value,)) \
                from None
        report = self._registry.Check(raw, newDir, source, dev=False,
                                      hostVersion=self._hostVersion)
        # The staged folder is "<id>.new": Check holds the id to its
        # folder's name, which the zip's "<id>/" already did (CheckZip).
        # Contract gap reported to the main session (ADR-009 L4).
        problems = tuple(
            problem for problem in report.problems
            if not (problem.code == ManifestCheck.ID_FOLDER_MISMATCH
                    and report.manifest is not None
                    and report.manifest.id == pluginId))
        fatal = [problem.code.value for problem in problems
                 if problem.scope == ProblemScope.PLUGIN]
        if report.manifest is None or fatal:
            raise InstallError(MANIFEST_INVALID, fatal or (
                ManifestCheck.MANIFEST_UNREADABLE.value,))
        return ManifestReport(manifest=report.manifest, problems=problems)

    def Pending(self, pluginId: str) -> Optional[PendingInstall]:
        with self._lock:
            return self._pending.get(pluginId)

    def Discard(self, pending: PendingInstall) -> bool:
        """Drop a prepared install (consent refused): its .new removed.
        False when it is not the pending one (or being committed)."""
        with self._lock:
            if self._pending.get(pending.plugin_id) is not pending \
                    or pending.plugin_id in self._busy:
                return False
            del self._pending[pending.plugin_id]
        self._RemoveNew(pending.new_dir)
        logger.info(f"Plugin install: {pending.plugin_id} was discarded")
        return True

    def Commit(self, pending: PendingInstall) -> InstallRecord:
        """C.3 step 6 after consent: describe in ``.new`` -> swap ->
        install.json. RUNNING while a run of the plugin goes -- asked first
        and again just before the swap (PluginJobs holds no per-plugin
        reservation) -- keeps the install pending: press again once the run
        ended. Every other refusal drops it. Raises InstallError."""
        pluginId = pending.plugin_id
        # Asked outside the lock: another part's lock is never taken
        # inside this one.
        if self._Running(pluginId):
            raise InstallError(RUNNING)
        with self._lock:
            if self._pending.get(pluginId) is not pending:
                raise InstallError(NOT_PENDING)
            if pluginId in self._busy:
                raise InstallError(IN_PROGRESS)
            self._busy.add(pluginId)
            del self._pending[pluginId]
        try:
            return self._Commit(pending)
        except InstallError as error:
            if error.code == RUNNING:
                with self._lock:
                    self._pending[pluginId] = pending
            else:
                self._RemoveNew(pending.new_dir)
            raise
        except BaseException:
            self._RemoveNew(pending.new_dir)
            raise
        finally:
            self._Release(pluginId)

    def _Running(self, pluginId: str) -> bool:
        return self._jobs is not None and bool(self._jobs.Running(pluginId))

    def _Commit(self, pending: PendingInstall) -> InstallRecord:
        pluginId, manifest = pending.plugin_id, pending.manifest
        if self._isDevId(pluginId):
            # A dev folder added while consent was asked (D4).
            raise InstallError(InstallDecision.REFUSE_DEV_ID.value)
        if BlockedBy(pluginId, pending.version, pending.sha256,
                     pending.source.repo_id, self._blocklist()) is not None:
            raise InstallError(BLOCKED)
        run = self._runner.Describe(pending.new_dir, manifest.entry,
                                    dev=False)
        result = self._registry.JudgeDescribe(run, manifest)
        if result.failure is not None:
            logger.info(f"Plugin install: {pluginId}: describe failed "
                        f"({result.failure.value})")
            raise InstallError(DESCRIBE_FAILED, (result.failure.value,))
        # describe took up to a minute: a run may have started since. Asked
        # before anything of the old plugin is forgotten, so a RUNNING
        # leaves it whole.
        if self._Running(pluginId):
            raise InstallError(RUNNING)
        # Before the swap: another author's zip never meets the old
        # plugin's secrets, values or enabled hooks, even if the swap fails.
        if pending.decision == InstallDecision.NEW_INSTALL:
            self._ForgetForNewInstall(
                pluginId, keepData=pending.installed is None)
        elif pending.installed is not None \
                and pending.installed.consent_digest != pending.consent_digest \
                and self._hooks is not None:
            # Consent asked again (D.6): its hooks start disabled.
            try:
                self._hooks.Forget(pluginId, HookDisabledReason.RECONSENT)
            except OSError as error:
                raise InstallError(SECRETS_FAILED,
                                   detail=type(error).__name__) from None
        record = InstallRecord(source=pending.source,
                               version=pending.version,
                               sha256=pending.sha256,
                               consent_digest=pending.consent_digest)
        current = self.PluginDir(pluginId)
        try:
            ZipSafety.Swap(pending.new_dir, current,
                           current + ZipSafety.OLD_SUFFIX,
                           commit=lambda: self._WriteRecord(pluginId, record),
                           sleep=self._sleep)
        except OSError as error:
            logger.warning(f"Plugin install: {pluginId}: the swap failed "
                           f"({ZipSafety.Why(error)})")
            raise InstallError(SWAP_FAILED,
                               detail=type(error).__name__) from None
        logger.info(f"Plugin install: {pluginId} {pending.version} is in "
                    f"place")
        return record

    def _ForgetForNewInstall(self, pluginId: str, *, keepData: bool) -> None:
        """A new install: the hooks disabled, the secrets forgotten and
        everything in user_data/plugins/<id>/ but install.json removed --
        config.json, hooks.json's values, secrets.bin and, when another
        source was recorded (``keepData`` false), data/. With no record
        data/ stays: it is what a removal asked to keep, and nothing says
        whose it was. install.json stays until the swap writes the new one
        (a failed swap keeps the old plugin's record, which the blocklist
        matches by). SECRETS_FAILED when it cannot."""
        try:
            if self._hooks is not None:
                self._hooks.Forget(pluginId, HookDisabledReason.NEW_INSTALL)
            if self._secrets is not None:
                self._secrets.Forget(pluginId, dev=False)
            self._RemoveSecretsFile(pluginId)
            userDir = UserDir(self.userDataDir, pluginId, dev=False)
            if os.path.isdir(userDir):
                _RemoveChildren(userDir, keep=(INSTALL_FILE, DATA_DIR)
                                if keepData else (INSTALL_FILE,))
        except OSError as error:
            raise InstallError(SECRETS_FAILED,
                               detail=type(error).__name__) from None

    def _RemoveSecretsFile(self, pluginId: str) -> None:
        """secrets.bin gone, whatever PluginSecrets did. Raises OSError."""
        path = os.path.join(UserDir(self.userDataDir, pluginId, dev=False),
                            SECRETS_FILE)
        try:
            os.remove(path)
        except FileNotFoundError:
            pass

    @staticmethod
    def _RemoveFile(path: str) -> None:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as error:
            logger.warning(f"Plugin install: a temporary file could not be "
                           f"removed ({ZipSafety.Why(error)}); removed at "
                           f"the next start")

    # ── removal and the launch's sweep ─────────────────────────────────

    def Remove(self, pluginId: str, *, data: bool, runs: str) -> None:
        """Remove an installed plugin (C.6): its hooks and secrets first
        (secrets.bin always), then -- after asking again that no run of it
        goes -- what an install left (``<id>.new`` / ``.old`` / ``.part``:
        an ``.old`` left would come back at the next launch's sweep), its
        folder, its user_data folder (``data/`` kept unless ``data``; the
        folder itself when nothing is left), its runs' folders under the
        work folder (``runs``: delete / keep_out -- each run's ``out/``
        kept -- / keep). BUILTIN for the built-in vodsplit; RUNNING while a
        run of it goes (the caller cancels first); NOT_FOUND when nothing
        of it is there; SECRETS_FAILED / REMOVE_FAILED. Raises
        InstallError."""
        _RequireId(pluginId)
        if pluginId in BUILTIN_IDS:
            raise InstallError(BUILTIN)
        if runs not in _RUNS:
            raise InstallError(INVALID_REQUEST)
        folder = self.PluginDir(pluginId)
        leftovers = [folder + suffix for suffix in (
            ZipSafety.NEW_SUFFIX, ZipSafety.PART_SUFFIX, ZipSafety.OLD_SUFFIX)]
        userDir = UserDir(self.userDataDir, pluginId, dev=False)
        if self._Running(pluginId):
            raise InstallError(RUNNING)
        with self._lock:
            if pluginId in self._busy:
                raise InstallError(IN_PROGRESS)
            if not any(os.path.lexists(path)
                       for path in [folder, userDir, *leftovers]):
                raise InstallError(NOT_FOUND)
            self._busy.add(pluginId)
            stale = self._pending.pop(pluginId, None)
        try:
            if stale is not None:
                self._RemoveNew(stale.new_dir)
            try:
                if self._hooks is not None:
                    self._hooks.Forget(pluginId, HookDisabledReason.USER)
                if self._secrets is not None:
                    self._secrets.Forget(pluginId, dev=False)
                self._RemoveSecretsFile(pluginId)
            except OSError as error:
                raise InstallError(SECRETS_FAILED,
                                   detail=type(error).__name__) from None
            if self._Running(pluginId):
                raise InstallError(RUNNING)
            try:
                for path in leftovers:
                    if os.path.isdir(path) and not os.path.islink(path):
                        ZipSafety.RemoveTree(path)
                    elif os.path.lexists(path):
                        os.remove(path)
                if os.path.lexists(folder):
                    ZipSafety.RemoveTree(folder)
                if os.path.isdir(userDir):
                    if data:
                        ZipSafety.RemoveTree(userDir)
                    else:
                        _RemoveChildren(userDir, keep=(DATA_DIR,))
                        if not os.listdir(userDir):
                            os.rmdir(userDir)
                self._RemoveRuns(pluginId, runs)
            except OSError as error:
                logger.warning(f"Plugin removal: {pluginId} was partly "
                               f"removed ({ZipSafety.Why(error)})")
                raise InstallError(REMOVE_FAILED,
                                   detail=type(error).__name__) from None
        finally:
            self._Release(pluginId)
        logger.info(f"Plugin removal: {pluginId} was removed")

    def _RemoveRuns(self, pluginId: str, runs: str) -> None:
        work = self._workDir()
        if runs == RUNS_KEEP or not work:
            return
        folder = os.path.join(work, PLUGINS_DIR, pluginId)
        if not os.path.isdir(folder):
            return
        if runs == RUNS_DELETE:
            ZipSafety.RemoveTree(folder)
            return
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if os.path.isdir(path) and not os.path.islink(path):
                _RemoveChildren(path, keep=(OUT_DIR,))
            else:
                os.remove(path)

    def Sweep(self) -> None:
        """At launch (E.2): what an install a crash ended left --
        ``<id>.part``, ``<id>.new`` and an upload's private copy removed,
        ``<id>.old`` put back when ``<id>`` is missing, else removed.
        Logged, never raises.

        A known window: a crash after the swap moved the new folder in and
        before install.json was replaced leaves the new folder with the
        old record (its version and SHA-256), which the next install or a
        block by SHA-256 reads as the old one's."""
        try:
            names = os.listdir(self.pluginsDir)
        except OSError:
            return
        with self._lock:
            held = set(self._busy) | set(self._pending)
        for name in names:
            if _UPLOAD.fullmatch(name):
                self._RemoveFile(os.path.join(self.pluginsDir, name))
        for pluginId in sorted({match.group(1) for match in
                                map(_LEFTOVER.fullmatch, names) if match}):
            if pluginId in held:
                continue
            ZipSafety.CleanSwap(os.path.join(self.pluginsDir, pluginId),
                                sleep=self._sleep)


def _RequireId(pluginId: Any) -> None:
    if not isinstance(pluginId, str) or not _PLUGIN_ID.fullmatch(pluginId):
        raise InstallError(INVALID_REQUEST)


def _CopyHashing(source: str, target: str, size: int) -> str:
    """Copy ``source`` to a new file ``target`` (at most ``size`` bytes: a
    file that grows on the way is not read further) and answer the SHA-256
    of what was written. Raises OSError."""
    digest = hashlib.sha256()
    left = size
    with open(source, "rb") as reader, open(target, "xb") as writer:
        while left > 0:
            chunk = reader.read(min(_CHUNK, left))
            if not chunk:
                break
            writer.write(chunk)
            digest.update(chunk)
            left -= len(chunk)
    return digest.hexdigest()


def _RemoveChildren(folder: str, *, keep: Sequence[str]) -> None:
    """Remove what is in ``folder`` but the names in ``keep``. Raises
    OSError."""
    for name in os.listdir(folder):
        if name in keep:
            continue
        path = os.path.join(folder, name)
        if os.path.isdir(path) and not os.path.islink(path):
            ZipSafety.RemoveTree(path)
        else:
            os.remove(path)
