"""The archive clipper's worker, installed and updated from Autona-releases
(ADR-006 A7).

The worker (core/ArchiveWorker.py: vodsplit frozen with PyInstaller) is not
in Autona's zip: it is a release of its own on Autona-releases, tagged
``worker-v<version>`` (not marked latest: Autona's updater reads
``releases/latest``), with two assets made by SmashVodSplitter's
packaging/build_worker.py:

- ``vodsplit-worker.json``, the manifest: ``{"version", "sets_format",
  "zip": {"name", "size", "sha256"}, "min_autona"?}``;
- ``vodsplit-worker-windows.zip``: the worker's folder as
  ``vodsplit-worker/...``.

The latest (``Snapshot`` / ``_Check``): the releases list
(``RELEASES_API_URL``, one request, unauthenticated) read on a task
executor job, at most every ``CHECK_MAX_AGE_SECS`` (a failure is tried
again after ``CHECK_RETRY_SECS``; the page's "check again" -- ``refresh``
-- at most every ``CHECK_REFRESH_FLOOR_SECS``). The newest ``worker-v``
release (not a draft or a prerelease) whose manifest says the sets.json
format this Autona reads (``ArchiveWorker.SUPPORTED_FORMAT``) and, when it
names one, an Autona version this one reaches (``min_autona``); newer ones
that do not are skipped, at most ``MANIFEST_CANDIDATES`` manifests are
read. Its assets must be downloaded from ``DOWNLOAD_PREFIX`` (the
release's own folder). There is no automatic update: the page reads
``Snapshot`` when it opens and the operator presses install.

Autona's end (the task executor's ``stopping``) is seen between the
check's requests and at every step of an install: every request's read
waits ``*_TIMEOUT[1]`` seconds at most (a download that times out, or
whose connection breaks, is resumed with a Range request:
``DOWNLOAD_RETRIES`` tries in a row without a byte), and the unpacked
worker's probe is killed. The process ends a few seconds after the
shutdown asks.

The install (``Install`` .. ``_Run``; one at a time, a task executor job,
cancelled by revision): refused when the release is the version
installed (``INSTALLED``); the zip is downloaded to
``vodsplit-worker.part`` (its size and SHA-256 checked against the
manifest), unpacked to ``vodsplit-worker.new/`` (an entry outside ``vodsplit-worker/``, one that
would leave the folder -- ``AssetPackService.ArchivePath`` --, a link or a
second entry of one name refuses the whole zip), the unpacked worker's
``version`` asked (``ArchiveWorker.ProbeCommand``: the use's rule, and the
manifest's version) and, when it has one, its self-check
(``ArchiveWorker.CheckCommand``: every part the frozen worker bundles),
then -- held by ArchiveJobService.ReserveSwap, so nothing of the worker
runs; a hold refused by a probe or a titles' preview, which end by
themselves, is asked again for up to ``SWAP_WAIT_SECS`` -- the current
``vodsplit-worker/`` is moved to ``vodsplit-worker.old/`` and the new one
moved in; the old one is removed once the new one is in place. A failure
before the swap leaves the current worker as it was; a failure in it puts
the old folder back. What a crash leaves (``CleanLeftovers``, at the next
start and before every install; each step on its own): the download and
``.new`` are removed, ``.old`` is put back when there is no
``vodsplit-worker/`` and removed when there is.

The folders are the current directory's (Autona's folder: ArchiveWorker's
``WorkerDir``), frozen or not. With ``AUTONA_VODSPLIT_CMD`` set the folder
is installed but not used (``env_command``: the page says so).

Messages are Japanese (the page shows them) and name no path. Qt-free.
Nothing is made, read or written at import or in the constructor.
"""
import hashlib
import json
import os
import re
import shutil
import stat
import threading
import time
import zipfile

import requests
from loguru import logger

from . import ArchiveWorker
from .AssetPackService import ArchivePath
from .TaskExecutor import TaskExecutor

# Where the releases are (AutonaUpdater.GITHUB_REPO's, pinned equal by
# tests/test_archive_worker_install.py: core does not import the updater).
RELEASES_REPO = "yuya-tagu/Autona-releases"
# One page of the releases, newest first. 100 (GitHub's most): Autona's own
# releases come between the worker's.
RELEASES_API_URL = f"https://api.github.com/repos/{RELEASES_REPO}/releases?per_page=100"
DOWNLOAD_PREFIX = f"https://github.com/{RELEASES_REPO}/releases/download/"
TAG_PATTERN = re.compile(r"worker-v([0-9]+\.[0-9]+\.[0-9]+)")
MANIFEST_NAME = "vodsplit-worker.json"
ZIP_NAME = "vodsplit-worker-windows.zip"

CHECK_MAX_AGE_SECS = 600
CHECK_RETRY_SECS = 30
# The page's "check again" (refresh) is not held to CHECK_RETRY_SECS, only
# to this (a double click).
CHECK_REFRESH_FLOOR_SECS = 3
# requests' (connect, read) timeouts; the read one is per receive: short,
# so that a request a stalled server holds lets Autona's end through.
CHECK_TIMEOUT = (5, 5)
DOWNLOAD_TIMEOUT = (5, 5)
# A download is resumed (Range) this many times in a row without a byte
# received before it fails, DOWNLOAD_PAUSE_SECS apart.
DOWNLOAD_RETRIES = 10
DOWNLOAD_PAUSE_SECS = 1.0
# At most 5 manifests and the newest 100 releases (one page) are read:
# enough while a format lasts; an old Autona finds none (NO_RELEASE) once
# 5 newer releases of another format, or 100 releases, came after its own.
MANIFEST_CANDIDATES = 5
MAX_MANIFEST_BYTES = 64 * 1024
MAX_ZIP_BYTES = 2 * 1024 ** 3
MAX_UNPACKED_BYTES = 4 * 1024 ** 3
MAX_ENTRIES = 50_000
DISK_MARGIN_BYTES = 64 * 1024 * 1024
# A first run of a freshly unpacked worker: the antivirus reads its files.
INSTALL_PROBE_TIMEOUT_SECS = 60
# Its self-check loads everything it bundles and runs ffmpeg and deno.
INSTALL_CHECK_TIMEOUT_SECS = 120
# How long the swap asks the hold again while a probe or a titles' preview
# (a few seconds each) refuses it, SWAP_RETRY_SECS apart.
SWAP_WAIT_SECS = 10
SWAP_RETRY_SECS = 0.5
_CHUNK = 256 * 1024
# The download's reads: a read waits until this much came (or the read
# timeout), so a slow line still lets a stop through every few seconds.
_DOWNLOAD_CHUNK = 64 * 1024
_RENAME_ATTEMPTS = 6
_RENAME_BACKOFF = 0.1
_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "Autona"}

# What the folders next to the worker's are called (its name + these).
PART_SUFFIX = ".part"
NEW_SUFFIX = ".new"
OLD_SUFFIX = ".old"

# The check's states.
CHECK_IDLE = "idle"
CHECK_RUNNING = "checking"
CHECK_DONE = "done"
CHECK_ERROR = "error"

# The job's states and stages.
IDLE = "idle"
RUNNING = "running"
DONE = "done"
ERROR = "error"
CANCELLED = "cancelled"
DOWNLOAD = "download"
VERIFY = "verify"
EXTRACT = "extract"
PROBE = "probe"
SWAP = "swap"

# Refusals (InstallerError.code; v1 answers them: AutonaApiV1) and the
# job's / the check's error codes.
INVALID = "invalid"
BUSY = "busy"
STOPPING = "stopping"
NO_RELEASE = "no_release"
CHANGED = "changed"
NOT_RUNNING = "not_running"
INSTALLED = "installed"
FETCH_FAILED = "fetch_failed"
DOWNLOAD_FAILED = "download_failed"
SIZE_MISMATCH = "size_mismatch"
HASH_MISMATCH = "hash_mismatch"
BAD_ARCHIVE = "bad_archive"
UNSAFE_ARCHIVE = "unsafe_archive"
PROBE_FAILED = "probe_failed"
SWAP_FAILED = "swap_failed"
DISK_FULL = "disk_full"
STOPPED = "stopped"
FAILED = "failed"

FETCH_FAILED_TEXT = ("配布中の切り抜きワーカーを確認できません。"
                     "インターネットに接続できるか確かめてから、もう一度開いてください。")
NO_RELEASE_TEXT = "この Autona で使える切り抜きワーカーの配布が見つかりません。"
CHANGED_TEXT = "配布中の版が変わりました。表示を確かめてからもう一度押してください。"
STOPPING_TEXT = "Autona の終了中です。"
DISK_FULL_TEXT = "空き容量が足りません。Autona のフォルダのドライブを空けてから、もう一度押してください。"


class InstallerError(Exception):
    """An install or a cancel refused: ``code`` (the constants above) and
    the Japanese message the page shows."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class _Cancelled(Exception):
    pass


class _Stopped(Exception):
    pass


class _JobError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def VersionTuple(text):
    """``x.y.z`` (anything after the third number ignored: ``0.0.0-fake``)
    as a tuple of ints, or None."""
    if not isinstance(text, str):
        return None
    match = re.match(r"([0-9]+)\.([0-9]+)\.([0-9]+)", text.strip())
    return tuple(int(part) for part in match.groups()) if match else None


def _Newer(latest, installed):
    left, right = VersionTuple(latest), VersionTuple(installed)
    return left is not None and right is not None and left > right


def _ErrorText(error):
    # An OSError's text names the file (a path of this PC); its reason
    # alone does not.
    if isinstance(error, OSError):
        return error.strerror or type(error).__name__
    return type(error).__name__


def _Within(root, path):
    root = os.path.normcase(os.path.abspath(root))
    path = os.path.normcase(os.path.abspath(path))
    try:
        return os.path.commonpath([root, path]) == root and path != root
    except ValueError:
        return False


def _RemoveTree(path):
    """Remove folder `path` (read-only files included). Raises OSError."""
    def again(function, target, _failure):
        os.chmod(target, stat.S_IWRITE)
        function(target)
    shutil.rmtree(path, onerror=again)


def _Rename(source, target):
    """os.rename, tried again a few times: on Windows an antivirus that
    reads a file just written holds it for a moment. Raises OSError."""
    delay = _RENAME_BACKOFF
    for attempt in range(_RENAME_ATTEMPTS):
        try:
            os.rename(source, target)
            return
        except PermissionError:
            if attempt == _RENAME_ATTEMPTS - 1:
                raise
            time.sleep(delay)
            delay *= 2


def _RequireSpace(folder, needed):
    try:
        free = shutil.disk_usage(folder).free
    except OSError:
        return
    if free < needed + DISK_MARGIN_BYTES:
        raise _JobError(DISK_FULL, DISK_FULL_TEXT)


def _RangeStart(response):
    """Where a 206's Content-Range (``bytes START-END/TOTAL``) starts, or
    None."""
    match = re.fullmatch(r"bytes\s+([0-9]+)-[0-9]+/(?:[0-9]+|\*)",
                         (response.headers.get("Content-Range") or "").strip())
    return int(match.group(1)) if match else None


def _Manifest(data, tagVersion):
    """The manifest `data` as {"version", "size", "sha256", "min_autona"},
    or None when it is not one (or not the tag's version)."""
    if not isinstance(data, dict):
        return None
    zipInfo = data.get("zip")
    if not isinstance(zipInfo, dict):
        return None
    size, digest = zipInfo.get("size"), zipInfo.get("sha256")
    minAutona = data.get("min_autona")
    if data.get("version") != tagVersion or zipInfo.get("name") != ZIP_NAME:
        return None
    if type(size) is not int or not 0 < size <= MAX_ZIP_BYTES:
        return None
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}",
                                                        digest):
        return None
    if minAutona is not None and VersionTuple(minAutona) is None:
        return None
    fmt = data.get("sets_format")
    if isinstance(fmt, bool) or not isinstance(fmt, int):
        return None
    return {"version": tagVersion, "format": fmt, "size": size,
            "sha256": digest.lower(), "min_autona": minAutona}


class _Job:
    """One install's state; its fields under the installer's lock."""

    def __init__(self, revision, release):
        self.revision = revision
        self.release = release
        self.status = RUNNING
        self.stage = None
        self.done = 0
        self.total = 0
        self.error = None
        self.message = None
        self.cancel = threading.Event()

    def Snapshot(self):
        return {"revision": self.revision, "status": self.status,
                "version": self.release["version"] if self.release else None,
                "stage": self.stage, "done": self.done, "total": self.total,
                "error": self.error, "message": self.message}


class ArchiveWorkerInstaller:
    instance = None

    def __init__(self, jobs=None, autonaVersion=None,
                 releasesUrl=RELEASES_API_URL,
                 downloadPrefix=DOWNLOAD_PREFIX):
        # ArchiveJobService (its instance at use when None): the installed
        # version (CachedProbe) and the hold of the swap.
        self._jobs = jobs
        # Autona's own version (assets/versions.json, given by bootstrap),
        # for a manifest's min_autona.
        self._autonaVersion = autonaVersion or (lambda: None)
        self.releasesUrl = releasesUrl
        self.downloadPrefix = downloadPrefix
        self._lock = threading.Lock()
        # The worker's folders are changed by one at a time: an install's
        # job for its whole run, or CleanLeftovers. Never taken with _lock.
        self._filesLock = threading.Lock()
        # The latest release (a dict, or None: none found / not checked),
        # when it was checked and tried (time.monotonic()), its wall-clock
        # time, the last check's failure (a code) and whether one runs.
        self._latest = None
        self._checkedAt = None
        self._triedAt = None
        self._checkedWall = None
        self._checkError = None
        self._checking = False
        self._job = None
        self._revision = 0

    def _Jobs(self):
        if self._jobs is not None:
            return self._jobs
        from .ArchiveJobService import ArchiveJobService
        return ArchiveJobService.instance

    @staticmethod
    def _Paths():
        """(the worker's folder, .new, .old, the download)."""
        current = ArchiveWorker.WorkerDir()
        return (current, current + NEW_SUFFIX, current + OLD_SUFFIX,
                current + PART_SUFFIX)

    # ── what the page reads ────────────────────────────────────────────

    def Snapshot(self, refresh=False):
        """{installed: {available, version} of the last probe (None before
        the page's first status), env_command, latest: {version, size,
        published_at} or None, check: {status, error, message, checked_at},
        update_available, job: the last install's state}. Starts the
        latest's check in the background when it is due (or `refresh`,
        at most once every CHECK_RETRY_SECS); never waits for it."""
        self._MaybeCheck(refresh)
        jobs = self._Jobs()
        probe = jobs.CachedProbe() if jobs is not None else None
        with self._lock:
            latest = self._latest
            if self._checking:
                status = CHECK_RUNNING
            elif self._checkError is not None:
                status = CHECK_ERROR
            elif self._checkedAt is not None:
                status = CHECK_DONE
            else:
                status = CHECK_IDLE
            check = {"status": status, "error": self._checkError,
                     "message": FETCH_FAILED_TEXT if self._checkError
                     else None, "checked_at": self._checkedWall}
            job = self._job.Snapshot() if self._job is not None \
                else _Job(0, None).Snapshot() | {"status": IDLE}
        installed = None if probe is None else {
            "available": probe.available, "version": probe.version}
        return {
            "installed": installed,
            "env_command": ArchiveWorker.UsesEnvCommand(),
            "latest": None if latest is None else {
                "version": latest["version"], "size": latest["size"],
                "published_at": latest["published_at"]},
            "check": check,
            "update_available": bool(
                latest is not None and probe is not None and probe.available
                and _Newer(latest["version"], probe.version)),
            "job": job,
        }

    # ── the latest release ─────────────────────────────────────────────

    def _MaybeCheck(self, refresh):
        executor = TaskExecutor.instance
        if executor is None or executor.stopping.is_set():
            return
        with self._lock:
            if self._checking:
                return
            now = time.monotonic()
            tried = self._triedAt
            if tried is not None and now - tried < (
                    CHECK_REFRESH_FLOOR_SECS if refresh else CHECK_RETRY_SECS):
                return
            fresh = self._checkedAt is not None and self._checkError is None \
                and now - self._checkedAt < CHECK_MAX_AGE_SECS
            if fresh and not refresh:
                return
            self._checking = True
            self._triedAt = now
        try:
            executor.submit(self._Check, task_name="archive worker check")
        except Exception:
            logger.opt(exception=True).warning(
                "Archive worker: the release check could not be started")
            with self._lock:
                self._checking = False

    def _Check(self):
        """Read the releases and their manifests (a task executor job)."""
        latest, error = None, None
        try:
            latest = self._FindLatest()
        except _Stopped:
            logger.info("Archive worker: the release check stopped (Autona "
                        "is ending)")
            with self._lock:
                self._checking = False
            return
        except Exception as failure:
            error = FETCH_FAILED
            logger.warning(f"Archive worker: the release check failed: "
                           f"{str(failure) or type(failure).__name__}")
        with self._lock:
            self._checking = False
            if error is None:
                self._latest = latest
                self._checkedAt = time.monotonic()
                self._checkedWall = int(time.time())
            self._checkError = error
        if error is None:
            logger.info("Archive worker: latest release "
                        + (latest["version"] if latest else "none"))

    @staticmethod
    def _StepCheck():
        """Raise _Stopped once Autona is ending (the check's requests)."""
        executor = TaskExecutor.instance
        if executor is None or executor.stopping.is_set():
            raise _Stopped()

    def _FindLatest(self):
        """The newest usable release (see the module docstring), or None.
        Raises on a failed or unreadable releases list, _Stopped once
        Autona is ending."""
        self._StepCheck()
        response = requests.get(self.releasesUrl, headers=_HEADERS,
                                timeout=CHECK_TIMEOUT)
        response.raise_for_status()
        releases = response.json()
        if not isinstance(releases, list):
            raise ValueError("the releases list is not a JSON array")
        candidates = []
        for release in releases:
            if not isinstance(release, dict) or release.get("draft") \
                    or release.get("prerelease"):
                continue
            match = TAG_PATTERN.fullmatch(str(release.get("tag_name") or ""))
            if not match:
                continue
            assets = {asset.get("name"): asset
                      for asset in release.get("assets") or ()
                      if isinstance(asset, dict)}
            manifest, archive = assets.get(MANIFEST_NAME), assets.get(ZIP_NAME)
            if manifest is None or archive is None:
                continue
            candidates.append((VersionTuple(match.group(1)), match.group(1),
                               match.group(0), release, manifest, archive))
        candidates.sort(key=lambda item: item[0], reverse=True)
        autona = VersionTuple(self._autonaVersion() or "")
        read = 0
        for _key, version, tag, release, manifestAsset, archive in candidates:
            manifestUrl = manifestAsset.get("browser_download_url")
            zipUrl = archive.get("browser_download_url")
            folder = f"{self.downloadPrefix}{tag}/"
            if not all(isinstance(url, str) and url.startswith(folder)
                       for url in (manifestUrl, zipUrl)):
                logger.warning(f"Archive worker: release {version}'s assets "
                               f"are not under the release folder; skipped")
                continue
            if read == MANIFEST_CANDIDATES:
                logger.info(f"Archive worker: {read} manifests read and none "
                            f"usable; the older releases are not read")
                break
            read += 1
            self._StepCheck()
            manifest = self._ReadManifest(manifestUrl, version)
            if manifest is None:
                continue
            if manifest["format"] != ArchiveWorker.SUPPORTED_FORMAT:
                logger.info(f"Archive worker: release {version} is format "
                            f"{manifest['format']}; skipped")
                continue
            if manifest["min_autona"] is not None and (
                    autona is None
                    or VersionTuple(manifest["min_autona"]) > autona):
                logger.info(f"Archive worker: release {version} needs Autona "
                            f"{manifest['min_autona']}; skipped")
                continue
            if archive.get("size") != manifest["size"]:
                logger.warning(f"Archive worker: release {version}'s zip is "
                               f"not the manifest's size; skipped")
                continue
            return {**manifest, "url": zipUrl,
                    "published_at": release.get("published_at")}
        return None

    def _ReadManifest(self, url, version):
        """Release `version`'s manifest, or None (logged) when it cannot be
        read or is not one."""
        try:
            with requests.get(url, headers={"User-Agent": "Autona"},
                              stream=True, timeout=CHECK_TIMEOUT) as response:
                response.raise_for_status()
                body = b""
                for chunk in response.iter_content(8192):
                    body += chunk
                    if len(body) > MAX_MANIFEST_BYTES:
                        raise ValueError("the manifest is too large")
            manifest = _Manifest(json.loads(body.decode("utf-8")), version)
        except Exception as failure:
            logger.warning(f"Archive worker: release {version}'s manifest "
                           f"could not be read: "
                           f"{str(failure) or type(failure).__name__}")
            return None
        if manifest is None:
            logger.warning(f"Archive worker: release {version}'s manifest is "
                           f"not usable; skipped")
        return manifest

    # ── the install ────────────────────────────────────────────────────

    def Install(self, version):
        """Install the latest release, which the page showed as `version`
        -> the job's state (running). Raises InstallerError: INVALID,
        STOPPING, BUSY (an install runs, a job runs or the worker is being
        replaced; a probe or a titles' preview is waited for at the swap),
        INSTALLED (the worker in the folder is that version), NO_RELEASE
        (none known: the check has not found one), CHANGED (the latest is
        another version now)."""
        if not isinstance(version, str) or VersionTuple(version) is None:
            raise InstallerError(INVALID, "入れる版（version）を指定してください。")
        executor = TaskExecutor.instance
        if executor is None or executor.stopping.is_set():
            raise InstallerError(STOPPING, STOPPING_TEXT)
        jobs = self._Jobs()
        busy = jobs.SwapBusy() if jobs is not None else None
        if busy is not None:
            raise InstallerError(BUSY, busy)
        # The folder's worker (not the environment's command, which the
        # last probe would be).
        probe = jobs.CachedProbe() if jobs is not None else None
        if probe is not None and probe.available \
                and probe.version == version \
                and not ArchiveWorker.UsesEnvCommand():
            raise InstallerError(INSTALLED, (
                f"切り抜きワーカー {version} はもう入っています。"))
        with self._lock:
            if self._job is not None and self._job.status == RUNNING:
                raise InstallerError(BUSY, "切り抜きワーカーを入れているところです。")
            latest = self._latest
            if latest is None:
                raise InstallerError(NO_RELEASE, NO_RELEASE_TEXT)
            if latest["version"] != version:
                raise InstallerError(CHANGED, CHANGED_TEXT)
            self._revision += 1
            job = _Job(self._revision, dict(latest))
            self._job = job
        try:
            future = executor.submit(
                self._Run, job, task_name=f"archive worker install "
                                          f"#{job.revision}")
        except Exception as failure:
            logger.opt(exception=True).error(
                f"Archive worker install #{job.revision} could not be started")
            self._Finish(job, ERROR, FAILED, "切り抜きワーカーの導入を始められませんでした。")
            raise InstallerError(BUSY, "切り抜きワーカーの導入を始められませんでした。") \
                from failure

        def settleCancelled(done):
            # Cancelled by the shutdown before it ran.
            if done.cancelled():
                self._Finish(job, ERROR, STOPPED, STOPPING_TEXT)

        future.add_done_callback(settleCancelled)
        logger.info(f"Archive worker install #{job.revision}: "
                    f"{job.release['version']}")
        with self._lock:
            return job.Snapshot()

    def Cancel(self, revision):
        """Ask install `revision` to stop (seen at its next step, before the
        swap) -> its state. Raises InstallerError(NOT_RUNNING) when it is
        not the running one."""
        with self._lock:
            job = self._job
            if job is None or job.status != RUNNING \
                    or job.revision != revision:
                raise InstallerError(NOT_RUNNING, "その導入は実行中ではありません。")
            job.cancel.set()
            snapshot = job.Snapshot()
        logger.info(f"Archive worker install #{revision}: cancel asked")
        return snapshot

    def _Run(self, job):
        status, error, message = DONE, None, None
        with self._filesLock:
            try:
                self._CleanLeftovers()
                part = self._Download(job)
                self._Extract(job, part)
                self._ProbeNew(job)
                self._Swap(job)
                message = f"切り抜きワーカー {job.release['version']} を入れました。"
            except _Cancelled:
                status = CANCELLED
                message = "切り抜きワーカーの導入を中止しました。"
            except _Stopped:
                status, error, message = ERROR, STOPPED, STOPPING_TEXT
            except _JobError as failure:
                status, error, message = ERROR, failure.code, failure.message
            except OSError as failure:
                full = getattr(failure, "errno", None) == 28 or \
                    getattr(failure, "winerror", None) in (39, 112)
                status = ERROR
                error = DISK_FULL if full else FAILED
                message = DISK_FULL_TEXT if full else (
                    f"切り抜きワーカーを入れられませんでした（{_ErrorText(failure)}）。")
                logger.opt(exception=True).debug(
                    f"Archive worker install #{job.revision}: the failure")
            except Exception as failure:
                status, error = ERROR, FAILED
                message = (f"切り抜きワーカーを入れられませんでした"
                           f"（{_ErrorText(failure)}）。")
                logger.opt(exception=True).error(
                    f"Archive worker install #{job.revision} failed")
            finally:
                self._Discard()
        self._Finish(job, status, error, message)

    def _Discard(self):
        """Remove the download and an unpacked worker that was not moved in
        (logged when it cannot be: CleanLeftovers tries again)."""
        _current, new, _old, part = self._Paths()
        try:
            if os.path.lexists(part):
                os.remove(part)
            if os.path.isdir(new):
                _RemoveTree(new)
        except OSError as failure:
            logger.warning(f"Archive worker: a download could not be removed "
                           f"({_ErrorText(failure)}); removed at the next "
                           f"start")

    def _Finish(self, job, status, error, message):
        with self._lock:
            if job.status != RUNNING:
                return
            job.status, job.error, job.message = status, error, message
            job.stage = None
        logger.info(f"Archive worker install #{job.revision}: {status}"
                    + (f" ({error})" if error else ""))

    def _Set(self, job, **fields):
        with self._lock:
            for name, value in fields.items():
                setattr(job, name, value)

    @staticmethod
    def _Halted(job):
        """Whether Autona is ending or `job` was cancelled."""
        return TaskExecutor.instance.stopping.is_set() or job.cancel.is_set()

    @staticmethod
    def _Step(job):
        if TaskExecutor.instance.stopping.is_set():
            raise _Stopped()
        if job.cancel.is_set():
            raise _Cancelled()

    def _Pause(self, job, seconds):
        """Wait `seconds`, a step (_Step) at every SWAP_RETRY_SECS."""
        left = seconds
        while left > 0:
            self._Step(job)
            time.sleep(min(left, SWAP_RETRY_SECS))
            left -= SWAP_RETRY_SECS
        self._Step(job)

    def _Download(self, job):
        """Download the release's zip to the .part file, its size and
        SHA-256 checked -> its path. A request that times out or whose
        connection breaks is resumed where it stopped (Range); a server
        that answers the whole file again starts it over."""
        release = job.release
        current, _new, _old, part = self._Paths()
        size = release["size"]
        self._Step(job)
        _RequireSpace(os.path.dirname(current), size)
        self._Set(job, stage=DOWNLOAD, done=0, total=size)
        digest = hashlib.sha256()
        # Bytes written, the most ever written (a server that starts over
        # makes no progress until it passes it) and the tries in a row that
        # made none.
        done = best = 0
        idle = 0
        with open(part, "wb") as out:
            while True:
                self._Step(job)
                before = best
                headers = {"User-Agent": "Autona"}
                if done:
                    headers["Range"] = f"bytes={done}-"
                try:
                    with requests.get(release["url"], stream=True,
                                      allow_redirects=True, headers=headers,
                                      timeout=DOWNLOAD_TIMEOUT) as response:
                        status = response.status_code
                        if status not in (200, 206):
                            raise _JobError(DOWNLOAD_FAILED, (
                                f"切り抜きワーカーをダウンロードできません（HTTP "
                                f"{status}）。"))
                        if status == 206 and _RangeStart(response) != done:
                            raise _JobError(DOWNLOAD_FAILED, (
                                "切り抜きワーカーのダウンロードを続きから再開できません。"
                                "もう一度押してください。"))
                        if status == 200 and done:
                            # No range: the whole file again.
                            logger.info(f"Archive worker install "
                                        f"#{job.revision}: the server sent "
                                        f"the whole file again")
                            out.seek(0)
                            out.truncate()
                            digest, done = hashlib.sha256(), 0
                            self._Set(job, done=0)
                        for chunk in response.iter_content(_DOWNLOAD_CHUNK):
                            self._Step(job)
                            if not chunk:
                                continue
                            done += len(chunk)
                            if done > size:
                                raise _JobError(SIZE_MISMATCH, (
                                    "ダウンロードした切り抜きワーカーの大きさが配布の記載と違います。"))
                            out.write(chunk)
                            digest.update(chunk)
                            best = max(best, done)
                            self._Set(job, done=done)
                    break
                except requests.RequestException as failure:
                    self._Step(job)
                    idle = 0 if best > before else idle + 1
                    if idle >= DOWNLOAD_RETRIES:
                        logger.warning(f"Archive worker install "
                                       f"#{job.revision}: the download "
                                       f"failed: {failure}")
                        raise _JobError(DOWNLOAD_FAILED, (
                            "切り抜きワーカーをダウンロードできません。"
                            "インターネットに接続できるか確かめてから、"
                            "もう一度押してください。")) from None
                    logger.info(f"Archive worker install #{job.revision}: "
                                f"the download is resumed at {done} bytes "
                                f"({type(failure).__name__})")
                    self._Pause(job, DOWNLOAD_PAUSE_SECS)
        self._Set(job, stage=VERIFY)
        if done != size:
            raise _JobError(SIZE_MISMATCH, (
                "ダウンロードした切り抜きワーカーの大きさが配布の記載と違います。"))
        if digest.hexdigest() != release["sha256"]:
            raise _JobError(HASH_MISMATCH, (
                "ダウンロードした切り抜きワーカーが配布のものと一致しません（SHA-256）。"))
        return part

    def _Plan(self, archive):
        """The zip's entries as [(info, path parts under the worker's
        folder)] and their unpacked size. Raises _JobError."""
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES:
            raise _JobError(BAD_ARCHIVE, "切り抜きワーカーの zip のファイルが多すぎます。")
        plan, seen, total = [], set(), 0
        for info in infos:
            parts = ArchivePath(info.filename)
            if parts is None or parts[0] != ArchiveWorker.WORKER_DIR \
                    or stat.S_ISLNK(info.external_attr >> 16):
                raise _JobError(UNSAFE_ARCHIVE, (
                    "切り抜きワーカーの zip に、vodsplit-worker フォルダの外を指すものが"
                    "含まれています。"))
            rest = parts[1:]
            if not rest:
                continue
            key = tuple(part.casefold() for part in rest)
            if key in seen:
                raise _JobError(UNSAFE_ARCHIVE, (
                    "切り抜きワーカーの zip に同じ名前のファイルが重なっています。"))
            seen.add(key)
            if not info.is_dir():
                total += info.file_size
            plan.append((info, rest))
        if total > MAX_UNPACKED_BYTES:
            raise _JobError(BAD_ARCHIVE, "切り抜きワーカーの zip が大きすぎます。")
        if (ArchiveWorker.WORKER_EXECUTABLE.casefold(),) not in seen:
            raise _JobError(BAD_ARCHIVE, (
                "切り抜きワーカーの zip に vodsplit-worker.exe がありません。"))
        return plan, total

    def _Extract(self, job, part):
        """Unpack the zip at `part` to the .new folder."""
        current, new, _old, _part = self._Paths()
        try:
            archive = zipfile.ZipFile(part)
        except (zipfile.BadZipFile, OSError) as failure:
            raise _JobError(BAD_ARCHIVE, (
                f"切り抜きワーカーの zip を開けません（{_ErrorText(failure)}）。")) from None
        with archive:
            plan, total = self._Plan(archive)
            _RequireSpace(os.path.dirname(current), total)
            self._Set(job, stage=EXTRACT, done=0, total=total)
            os.makedirs(new)
            done = 0
            for info, rest in plan:
                self._Step(job)
                target = os.path.join(new, *rest)
                if not _Within(new, target):
                    raise _JobError(UNSAFE_ARCHIVE, (
                        "切り抜きワーカーの zip に、vodsplit-worker フォルダの外を指すものが"
                        "含まれています。"))
                if info.is_dir():
                    os.makedirs(target, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                try:
                    with archive.open(info) as source, \
                            open(target, "xb") as out:
                        while True:
                            chunk = source.read(_CHUNK)
                            if not chunk:
                                break
                            out.write(chunk)
                            done += len(chunk)
                            self._Set(job, done=done)
                            self._Step(job)
                except (zipfile.BadZipFile, EOFError, zipfile.LargeZipFile) \
                        as failure:
                    raise _JobError(BAD_ARCHIVE, (
                        f"切り抜きワーカーの zip が壊れています"
                        f"（{_ErrorText(failure)}）。")) from None

    def _ProbeNew(self, job):
        """Ask the unpacked worker its version (the use's rule): it must be
        usable and the release's version; then, when it has one, its
        self-check (a worker from before has none). Killed when Autona
        ends or the install is cancelled."""
        self._Step(job)
        self._Set(job, stage=PROBE)
        _current, new, _old, _part = self._Paths()
        command = [os.path.join(new, ArchiveWorker.WORKER_EXECUTABLE)]
        halted = lambda: self._Halted(job)  # noqa: E731
        try:
            probe = ArchiveWorker.ProbeCommand(
                command, INSTALL_PROBE_TIMEOUT_SECS, stop=halted)
            if not probe.available:
                raise _JobError(PROBE_FAILED, (
                    f"ダウンロードした切り抜きワーカーを使えません: {probe.reason}"))
            if probe.version != job.release["version"]:
                raise _JobError(PROBE_FAILED, (
                    f"ダウンロードした切り抜きワーカーの版（{probe.version}）が配布の記載"
                    f"（{job.release['version']}）と違います。"))
            self._Step(job)
            if ArchiveWorker.CHECK_FEATURE in probe.features:
                failed = ArchiveWorker.CheckCommand(
                    command, INSTALL_CHECK_TIMEOUT_SECS, stop=halted)
                if failed is not None:
                    raise _JobError(PROBE_FAILED, (
                        f"ダウンロードした切り抜きワーカーを使えません: {failed}"))
        except ArchiveWorker.ProbeStopped:
            self._Step(job)
            raise _Stopped() from None
        self._Step(job)

    def _Swap(self, job):
        """Move the current worker aside and the new one in, held by
        ArchiveJobService (nothing of the worker runs); the old one is put
        back when the new one cannot be moved in, removed once it is."""
        self._Step(job)
        self._Set(job, stage=SWAP)
        current, new, old, _part = self._Paths()
        jobs = self._Reserve(job)
        moved = False
        try:
            if os.path.isdir(old):
                if os.path.lexists(current):
                    _RemoveTree(old)
                else:
                    # A crash's, which CleanLeftovers could not put back:
                    # the old worker back first, as it would have.
                    _Rename(old, current)
            if os.path.lexists(current):
                _Rename(current, old)
                moved = True
            try:
                _Rename(new, current)
            except OSError as failure:
                if moved:
                    try:
                        _Rename(old, current)
                    except OSError:
                        logger.opt(exception=True).error(
                            "Archive worker: the old worker could not be put "
                            "back; the next start puts it back")
                raise _JobError(SWAP_FAILED, (
                    f"切り抜きワーカーを入れ替えられません（{_ErrorText(failure)}）。"
                    "切り抜きワーカーを使っているほかのアプリを閉じてから、もう一度押してください。")) \
                    from None
        except OSError as failure:
            raise _JobError(SWAP_FAILED, (
                f"切り抜きワーカーを入れ替えられません（{_ErrorText(failure)}）。"
                "切り抜きワーカーを使っているほかのアプリを閉じてから、もう一度押してください。")) \
                from None
        finally:
            jobs.ReleaseSwap()
        logger.info(f"Archive worker install #{job.revision}: "
                    f"{job.release['version']} is in place")
        if moved:
            try:
                _RemoveTree(old)
            except OSError as failure:
                logger.warning(f"Archive worker: the old worker could not be "
                               f"removed ({_ErrorText(failure)}); removed at "
                               f"the next start")

    def _Reserve(self, job):
        """ArchiveJobService's hold of the worker for the swap -> the
        service. A refusal that ends by itself (a probe, a titles' preview:
        ``transient``) is asked again for up to SWAP_WAIT_SECS. Raises
        _Stopped (Autona is ending), _Cancelled, _JobError(BUSY)."""
        from .ArchiveJobService import ArchiveError, STOPPING as JOBS_STOPPING
        jobs = self._Jobs()
        waited = 0.0
        while True:
            try:
                jobs.ReserveSwap()
                return jobs
            except ArchiveError as refused:
                if refused.code == JOBS_STOPPING:
                    raise _Stopped() from None
                if not refused.transient or waited >= SWAP_WAIT_SECS:
                    raise _JobError(BUSY, refused.message) from None
                if not waited:
                    logger.info(f"Archive worker install #{job.revision}: "
                                f"the swap waits ({refused.message})")
            self._Pause(job, SWAP_RETRY_SECS)
            waited += SWAP_RETRY_SECS

    # ── what a crash leaves ────────────────────────────────────────────

    def CleanLeftovers(self):
        """Remove what an install a crash ended left (the download, .new),
        and put .old back when the worker's folder is missing (else remove
        it). Logged, never raises: the shells' start calls it."""
        with self._filesLock:
            try:
                self._CleanLeftovers()
            except Exception:
                logger.opt(exception=True).error(
                    "Archive worker: what an install left could not be "
                    "cleaned")

    def _CleanLeftovers(self):
        """Each step on its own: one that fails does not keep the others
        from running."""
        current, new, old, part = self._Paths()
        try:
            if os.path.lexists(part):
                os.remove(part)
                logger.info("Archive worker: an unfinished download was "
                            "removed")
        except OSError as failure:
            logger.warning(f"Archive worker: an unfinished download could "
                           f"not be removed ({_ErrorText(failure)})")
        try:
            if os.path.isdir(new):
                _RemoveTree(new)
                logger.info("Archive worker: an unfinished install was "
                            "removed")
        except OSError as failure:
            logger.warning(f"Archive worker: an unfinished install could "
                           f"not be removed ({_ErrorText(failure)})")
        try:
            if os.path.isdir(old):
                if os.path.lexists(current):
                    _RemoveTree(old)
                    logger.info("Archive worker: the previous worker left "
                                "by an install was removed")
                else:
                    _Rename(old, current)
                    logger.warning("Archive worker: the previous worker was "
                                   "put back (an install did not finish)")
        except OSError as failure:
            logger.warning(f"Archive worker: the previous worker left by an "
                           f"install could not be cleaned "
                           f"({_ErrorText(failure)})")
