"""The plugin installs as the v1 routes drive them (ADR-009 P5a, appendix
C.3 / C.5 / C.6; wiring I4): PluginTypes.PluginInstalls over
PluginInstaller.

- **The upload** (``ReceiveUpload``): the zip's bytes, read on the request's
  thread into a private file in the plugins folder (PluginInstaller's
  ``.upload-<16 hex>.part``, which its launch sweep removes), exactly the
  Content-Length, the free space asked first. One is held at a time, for
  UPLOAD_EXPIRY_SECS; a new upload replaces it (one still arriving fails).
  Its id is a secret token (the op log cuts it: AutonaApiV1).
- **An install** (``StartInstall`` -> ``Consent`` / ``Cancel``): one at a
  time. PREPARING is a TaskExecutor job (``PrepareFromZip``: the zip copied,
  checked, unpacked to ``<id>.new``, its manifest checked; the upload's file
  removed whatever happens), then CONSENT with the sheet (PendingInstall).
  Consent compares the page's digest with the pending one's (D6: the page
  consents to what it showed) and runs the commit as a job: the plugin's
  starts held (PluginJobs.Hold), the commit (the staged plugin.json checked
  against the sheet again, describe in .new, the swap, install.json),
  ``registry.Scan()`` and ``hooks.Refresh()``, then the hold given back --
  so no run starts from the old manifest after the swap: DONE. A commit
  refused because a run of the plugin goes (RUNNING) keeps the install
  pending: back to CONSENT with the failure. A new install while one waits
  for consent discards the waiting one. Cancel discards the sheet; one
  cancelled while preparing is discarded when its job ends (a new install
  waits for that job).
- **The enable** (``SetEnabled``): an installed plugin only (never the
  built-in or a dev one). A disable is held (PluginJobs.Hold): written,
  its runs cancelled, then given back -- a start planned before the write
  is refused at its lane (its pending hooks are discarded when they would
  start: contract hooks.disabled_reasons).
- **A removal** (``StartRemoval``): a job -- the plugin's starts held
  (PluginJobs.Hold: no run, no pending hook starts), its runs cancelled and
  waited for (up to REMOVAL_WAIT_SECS: the cancel's grace and the job
  object; a run writing its folder counts), PluginInstaller.Remove (which
  forgets the hooks, REMOVED), ``registry.Scan()`` and ``hooks.Refresh()``,
  then the hold given back. ``Removal`` answers what it would remove and
  how the last one went -- a failed one also once the plugin left the list
  (a partial removal), and it can be asked again.
- One change of a plugin at a time: its commit, its removal and its enable
  refuse each other (BUSY).
- **The blocklist** (``SetBlocklist`` / ``Blocklist`` / ``BlockedReason``):
  the rows in effect (``ReadListingBlocklist``: the previous copy
  ``user_data/plugins/_listing.json``, else the copy shipped with Autona;
  fetching it is P5b's). The registry's ``blockedBy`` and the installer's
  ``blocklist`` read them; an installed plugin newly matching has its runs
  cancelled (under a hold, as a disable) and its hooks forgotten (BLOCKED).
  A dev folder and the built-in are never blocked. install.json's reads
  for it are kept until the next change (install, removal, scan here).
- **The launch** (``Sweep``, a job): PluginInstaller.Sweep, a scan when it
  put a folder back. Uploads and installs are refused until it has run.

Refusals raise PluginInstallsError (a StartRefusal and words without a path
or a value); what a job fails with is the install's / removal's
InstallFailure (PluginTypes.INSTALL_ERROR_REASONS).

Threads: the uploads' reads run on the request's thread; the prepare, the
commit and the removal are TaskExecutor jobs (none submits another). The
lock is a leaf: no installer / registry / jobs / hooks call is made while
holding it. Logs: the plugin id and the outcome, never a path, a token or a
value (E.3).

No Qt; nothing is made, read or written on import (pitfalls #1).
"""

import json
import os
import secrets
import shutil
import threading
import time
from typing import Any, Callable, Optional, Sequence, Tuple

from loguru import logger

from .PluginInstaller import (DISK_MARGIN_BYTES, NOT_FOUND, RUNNING,
                              InstallError, ParseBlocklist, BlockedBy)
from .PluginTypes import (INSTALL_ERROR_REASONS, ZIP_MAX_BYTES, Badge,
                          BlockEntry, InstallFailure, InstallState,
                          InstallStatus, RemovalState, RemovalStatus,
                          StartRefusal, Text)

UPLOAD_EXPIRY_SECS = 15 * 60
UPLOAD_CHUNK = 1024 * 1024
# How long a removal waits for the plugin's cancelled runs to end: the
# cancel's grace (10 s, ADR-009 D6) and the job object's end after it.
REMOVAL_WAIT_SECS = 20.0
_REMOVAL_POLL_SECS = 0.1
# The listing's copy read for its blocked rows (plugins.json is small).
LISTING_MAX_BYTES = 4 * 1024 * 1024
LISTING_COPY = "_listing.json"
# The copy shipped with Autona (its release's CI takes it from
# Autona-releases; none in a source checkout: no row).
BUNDLED_LISTING = "./assets/plugins.json"
# An unexpected exception of a job (INSTALL_ERROR_REASONS' own code).
FAILED = "failed"
# The reason shown for a blocked row without one.
_BLOCKED_TEXT = {"ja": "遮断されています。", "en": "Blocked."}
_INSTALLED_BADGES = (Badge.LISTED, Badge.MANUAL)


class PluginInstallsError(Exception):
    """A request refused: ``reason`` (a StartRefusal) and ``detail`` (no
    path or value)."""

    def __init__(self, reason: StartRefusal, detail: str = ""):
        super().__init__(f"{reason.value}: {detail}" if detail
                         else reason.value)
        self.reason = reason
        self.detail = detail


def Failure(error: BaseException) -> InstallFailure:
    """An exception as the install's / removal's failure: an InstallError
    by its code, anything else FAILED (its type alone: its text may name a
    path)."""
    if isinstance(error, InstallError):
        code = error.code if error.code in INSTALL_ERROR_REASONS else FAILED
        return InstallFailure(code=code, reason=INSTALL_ERROR_REASONS[code],
                              problems=tuple(error.problems),
                              detail=error.detail)
    return InstallFailure(code=FAILED, reason=INSTALL_ERROR_REASONS[FAILED],
                          detail=type(error).__name__)


def ReadListingBlocklist(paths: Sequence[str]) -> Tuple[BlockEntry, ...]:
    """The ``blocked`` rows of the first listing copy of ``paths`` that can
    be read as a JSON object (at most LISTING_MAX_BYTES); () when none can.
    Logged by its outcome only."""
    for path in paths:
        try:
            with open(path, "rb") as file:
                content = file.read(LISTING_MAX_BYTES + 1)
        except OSError:
            continue
        if len(content) > LISTING_MAX_BYTES:
            logger.warning("Plugins: a listing copy is too large; skipped")
            continue
        try:
            data = json.loads(content.decode("utf-8-sig"))
        except ValueError:
            logger.warning("Plugins: a listing copy is not JSON; skipped")
            continue
        if isinstance(data, dict):
            rows = ParseBlocklist(data)
            logger.info(f"Plugins: {len(rows)} blocked row(s) in effect")
            return rows
    return ()


class _Install:
    """One install's state; its fields under PluginInstalls' lock."""

    def __init__(self, installId):
        self.id = installId
        self.state = InstallState.PREPARING
        self.pluginId = None
        self.pending = None
        self.failure = None

    def Status(self):
        return InstallStatus(install_id=self.id, state=self.state,
                             plugin_id=self.pluginId, pending=self.pending,
                             failure=self.failure)


class PluginInstalls:
    """``installer``: PluginInstaller. ``registry``: PluginRegistry (Get,
    Entries, Scan). ``jobs``: PluginJobs (Running, Hold / Unhold,
    CancelAll). ``hooks``: PluginHooks (Refresh, Forget) or None (headless,
    or not wired yet). ``executor``: a TaskExecutor (``submit``,
    ``stopping``); TaskExecutor.instance at use when None. ``clock`` /
    ``sleep``: time.monotonic / time.sleep."""

    # The one bootstrap_core() makes (ADR-009 E.1); None until then.
    instance = None

    def __init__(self, installer, registry, *, jobs=None, hooks=None,
                 executor=None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self._installer = installer
        self._registry = registry
        self._jobs = jobs
        self._hooks = hooks
        self._executor = executor
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._swept = False
        # The upload held: {"id", "path", "expiresAt"} or None; the
        # generation of the last one begun (a newer one supersedes).
        self._upload = None
        self._uploadGeneration = 0
        self._install: Optional[_Install] = None
        # Jobs of installs still running (a cancelled one's prepare too).
        self._installJobs = 0
        # plugin id -> (RemovalState, InstallFailure or None)
        self._removals: dict = {}
        # plugin id -> the last RemovalSizes answer (answered while it is
        # being removed, and by the removal's start).
        self._sizes: dict = {}
        # Plugin ids whose enable is being written.
        self._enabling: set = set()
        # plugin id -> install.json's record (or None) for BlockedReason,
        # dropped at each change.
        self._records: dict = {}
        self._blocklist: Tuple[BlockEntry, ...] = ()
        self._blocked: frozenset = frozenset()

    # ── shared ─────────────────────────────────────────────────────────

    def _Executor(self):
        if self._executor is not None:
            return self._executor
        from .TaskExecutor import TaskExecutor
        return TaskExecutor.instance

    def _Stopping(self) -> bool:
        executor = self._Executor()
        return executor is None or executor.stopping.is_set()

    def _Ready(self):
        if self._Stopping():
            raise PluginInstallsError(StartRefusal.UNAVAILABLE,
                                      "shutting down")
        with self._lock:
            swept = self._swept
        if not swept:
            raise PluginInstallsError(StartRefusal.UNAVAILABLE,
                                      "the plugins folder is being checked")

    def _Submit(self, fn, name, onCancelled):
        """Submit ``fn`` as a job; ``onCancelled()`` when the shutdown
        cancels it before it runs. False when it could not be submitted."""
        try:
            future = self._Executor().submit(fn, task_name=name)
        except Exception as error:
            logger.warning(f"Plugins: {name} could not be started "
                           f"({type(error).__name__})")
            return False

        def settled(done):
            if done.cancelled():
                onCancelled()

        future.add_done_callback(settled)
        return True

    def _AfterChange(self):
        """The registry read again and the hooks after it (never inside the
        scan: PluginHooks.Refresh). Called while the plugin's starts are
        held."""
        with self._lock:
            self._records.clear()
        try:
            self._registry.Scan()
        except Exception as error:
            logger.warning(f"Plugins: the scan after a change failed "
                           f"({type(error).__name__})")
        if self._hooks is not None:
            try:
                self._hooks.Refresh()
            except Exception as error:
                logger.warning(f"Plugins: the hooks' refresh failed "
                               f"({type(error).__name__})")

    def _Installed(self, pluginId):
        """The installed plugin (not the built-in or a dev one): NOT_FOUND /
        INVALID_REQUEST."""
        plugin = self._registry.Get(pluginId) \
            if isinstance(pluginId, str) else None
        if plugin is None:
            raise PluginInstallsError(StartRefusal.NOT_FOUND,
                                      "no such plugin")
        if plugin.badge not in _INSTALLED_BADGES:
            raise PluginInstallsError(StartRefusal.INVALID_REQUEST,
                                      "not an installed plugin")
        return plugin

    def _ChangingLocked(self, pluginId, *, install=True, removal=True,
                        enable=True):
        """Why the plugin cannot be changed now (its commit, removal or
        enable going), or None. Under the lock."""
        current = self._install
        if install and current is not None \
                and current.pluginId == pluginId \
                and current.state == InstallState.INSTALLING:
            return "the plugin is being installed"
        if removal and self._removals.get(
                pluginId, (RemovalState.IDLE,))[0] == RemovalState.REMOVING:
            return "the plugin is being removed"
        if enable and pluginId in self._enabling:
            return "the plugin's enable is being written"
        return None

    def _Hold(self, pluginId) -> bool:
        """PluginJobs.Hold, when there are runs to hold (False: none)."""
        if self._jobs is None:
            return False
        self._jobs.Hold(pluginId)
        return True

    # ── the launch ─────────────────────────────────────────────────────

    def Sweep(self) -> None:
        with self._lock:
            self._records.clear()
        try:
            if self._installer.Sweep():
                self._AfterChange()
        except Exception as error:
            logger.warning(f"Plugins: the install sweep failed "
                           f"({type(error).__name__})")
        finally:
            with self._lock:
                self._swept = True

    # ── the upload ─────────────────────────────────────────────────────

    def ReceiveUpload(self, stream: Any, length: int) -> Tuple[str, int]:
        if not isinstance(length, int) or isinstance(length, bool) \
                or length <= 0:
            raise PluginInstallsError(StartRefusal.INVALID_REQUEST,
                                      "the upload is empty")
        if length > ZIP_MAX_BYTES:
            raise PluginInstallsError(
                StartRefusal.INVALID_REQUEST,
                f"a plugin zip is at most {ZIP_MAX_BYTES} bytes")
        self._Ready()
        try:
            path = self._installer.UploadPath()
        except OSError:
            raise PluginInstallsError(
                StartRefusal.UNAVAILABLE,
                "the plugins folder could not be made") from None
        with self._lock:
            replaced = self._upload
        reclaim = 0
        if replaced is not None:
            try:
                reclaim = os.path.getsize(replaced["path"])
            except OSError:
                pass
        try:
            free = shutil.disk_usage(os.path.dirname(path)).free
        except OSError:
            free = None
        if free is not None and free + reclaim < length + DISK_MARGIN_BYTES:
            raise PluginInstallsError(StartRefusal.UNAVAILABLE, "disk_full")
        with self._lock:
            previous, self._upload = self._upload, None
            self._uploadGeneration += 1
            generation = self._uploadGeneration
        if previous is not None:
            self._RemoveFile(previous["path"])
        held = False
        try:
            received = self._Receive(stream, length, path, generation)
            uploadId = secrets.token_urlsafe(16)
            with self._lock:
                if generation != self._uploadGeneration:
                    raise PluginInstallsError(StartRefusal.BUSY,
                                              "a newer upload replaced it")
                self._upload = {"id": uploadId, "path": path,
                                "expiresAt": self._clock()
                                + UPLOAD_EXPIRY_SECS}
                held = True
        finally:
            if not held:
                self._RemoveFile(path)
        logger.info(f"Plugin upload: received {received} bytes")
        return uploadId, UPLOAD_EXPIRY_SECS

    def _Receive(self, stream, length, path, generation) -> int:
        """Exactly ``length`` bytes of ``stream`` into a new ``path``."""
        left = length
        try:
            with open(path, "xb") as file:
                while left > 0:
                    with self._lock:
                        superseded = generation != self._uploadGeneration
                    if superseded:
                        raise PluginInstallsError(
                            StartRefusal.BUSY, "a newer upload replaced it")
                    if self._Stopping():
                        raise PluginInstallsError(StartRefusal.UNAVAILABLE,
                                                  "shutting down")
                    try:
                        chunk = stream.read(min(UPLOAD_CHUNK, left))
                    except Exception as error:
                        # The client went away (werkzeug's
                        # ClientDisconnected, a reset socket).
                        raise PluginInstallsError(
                            StartRefusal.INVALID_REQUEST,
                            f"the upload is incomplete "
                            f"({type(error).__name__})") from None
                    if not chunk:
                        break
                    file.write(chunk)
                    left -= len(chunk)
        except PluginInstallsError:
            raise
        except OSError as error:
            full = getattr(error, "errno", None) == 28 \
                or getattr(error, "winerror", None) in (39, 112)
            raise PluginInstallsError(
                StartRefusal.UNAVAILABLE,
                "disk_full" if full else "the upload could not be written") \
                from None
        if left:
            raise PluginInstallsError(StartRefusal.INVALID_REQUEST,
                                      "the upload is incomplete")
        return length

    def _HeldUpload(self, uploadId, take):
        """(the upload held by that id or None, an expired one to remove);
        ``take`` takes it out of the slot. Under the lock."""
        slot = self._upload
        if slot is not None and self._clock() >= slot["expiresAt"]:
            self._upload = None
            return None, slot
        if slot is None or not isinstance(uploadId, str) or \
                not secrets.compare_digest(slot["id"].encode(),
                                           uploadId.encode()):
            return None, None
        if take:
            self._upload = None
        return slot, None

    def DropUpload(self, uploadId: str) -> bool:
        with self._lock:
            slot, expired = self._HeldUpload(uploadId, take=True)
        for gone in (slot, expired):
            if gone is not None:
                self._RemoveFile(gone["path"])
        if slot is not None:
            logger.info("Plugin upload: dropped")
        return slot is not None

    @staticmethod
    def _RemoveFile(path):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as error:
            logger.warning(f"Plugins: an upload could not be removed "
                           f"({type(error).__name__}); removed at the next "
                           f"start")

    # ── an install ─────────────────────────────────────────────────────

    def StartInstall(self, uploadId: str) -> InstallStatus:
        self._Ready()
        with self._lock:
            current = self._install
            if self._installJobs or (current is not None and current.state in (
                    InstallState.PREPARING, InstallState.INSTALLING)):
                raise PluginInstallsError(StartRefusal.BUSY,
                                          "an install is going")
            slot, expired = self._HeldUpload(uploadId, take=True)
            if slot is None:
                waiting = None
            else:
                waiting = current if current is not None \
                    and current.state == InstallState.CONSENT else None
                if waiting is not None:
                    waiting.state = InstallState.CANCELLED
                install = _Install(secrets.token_urlsafe(12))
                self._install = install
                self._installJobs += 1
        if expired is not None:
            self._RemoveFile(expired["path"])
        if slot is None:
            raise PluginInstallsError(StartRefusal.NOT_FOUND,
                                      "no such upload")
        if waiting is not None and waiting.pending is not None:
            # One sheet at a time: the one waiting is declined.
            self._installer.Discard(waiting.pending)
        path = slot["path"]
        if not self._Submit(lambda: self._Prepare(install, path),
                            "plugin install: prepare",
                            lambda: self._Settle(install, path, None,
                                                 InstallError("stopped"))):
            self._Settle(install, path, None, InstallError("stopped"))
        with self._lock:
            return install.Status()

    def _Prepare(self, install, path):
        pending, error = None, None
        try:
            pending = self._installer.PrepareFromZip(path)
        except Exception as caught:  # an InstallError, or the unexpected
            error = caught
            if not isinstance(caught, InstallError):
                logger.opt(exception=True).debug("Plugin install: the "
                                                 "failure")
        self._Settle(install, path, pending, error)

    def _Settle(self, install, path, pending, error):
        """The prepare's end: the upload's file removed; CONSENT, FAILED,
        or -- cancelled meanwhile -- the staged folder discarded."""
        self._RemoveFile(path)
        with self._lock:
            self._installJobs = max(0, self._installJobs - 1)
            cancelled = install.state == InstallState.CANCELLED
            if not cancelled:
                if error is not None:
                    install.state = InstallState.FAILED
                    install.failure = Failure(error)
                else:
                    install.state = InstallState.CONSENT
                    install.pending = pending
                    install.pluginId = pending.plugin_id
        if cancelled and pending is not None:
            self._installer.Discard(pending)
        outcome = "cancelled" if cancelled else install.state.value
        logger.info(f"Plugin install: prepared ({outcome})")

    def _Find(self, installId):
        """The install by its id (under the lock)."""
        install = self._install
        if install is None or not isinstance(installId, str) or \
                not secrets.compare_digest(install.id.encode(),
                                           installId.encode()):
            raise PluginInstallsError(StartRefusal.NOT_FOUND,
                                      "no such install")
        return install

    def Status(self, installId: str) -> InstallStatus:
        with self._lock:
            return self._Find(installId).Status()

    def Consent(self, installId: str, consentDigest: str) -> InstallStatus:
        if self._Stopping():
            raise PluginInstallsError(StartRefusal.UNAVAILABLE,
                                      "shutting down")
        with self._lock:
            install = self._Find(installId)
            if install.state == InstallState.INSTALLING:
                raise PluginInstallsError(StartRefusal.BUSY,
                                          "the install is going")
            if install.state != InstallState.CONSENT:
                raise PluginInstallsError(
                    StartRefusal.INVALID_REQUEST,
                    f"the install is {install.state.value}")
            pending = install.pending
            if not isinstance(consentDigest, str) or \
                    consentDigest != pending.consent_digest:
                raise PluginInstallsError(
                    StartRefusal.INVALID_REQUEST,
                    "the consent is not for this sheet")
            busy = self._ChangingLocked(pending.plugin_id, install=False)
            if busy is not None:
                raise PluginInstallsError(StartRefusal.BUSY, busy)
        # Asked here too, so a press while a run goes answers at once (the
        # commit asks again under the hold).
        if self._jobs is not None and self._jobs.Running(pending.plugin_id):
            raise PluginInstallsError(StartRefusal.BUSY,
                                      "a run of the plugin is going")
        with self._lock:
            if self._install is not install \
                    or install.state != InstallState.CONSENT:
                raise PluginInstallsError(StartRefusal.BUSY,
                                          "the install changed")
            busy = self._ChangingLocked(pending.plugin_id, install=False)
            if busy is not None:
                raise PluginInstallsError(StartRefusal.BUSY, busy)
            install.state = InstallState.INSTALLING
            install.failure = None
            self._installJobs += 1
        if not self._Submit(lambda: self._Commit(install),
                            "plugin install: commit",
                            lambda: self._Committed(
                                install, InstallError("stopped"))):
            self._Committed(install, InstallError("stopped"))
        with self._lock:
            return install.Status()

    def _Commit(self, install):
        pluginId = install.pending.plugin_id
        error = None
        held = False
        try:
            # Held from before the swap to after the scan: no start of the
            # plugin reads the old manifest once the new folder is in.
            held = self._Hold(pluginId)
            if held and self._jobs.Running(pluginId):
                raise InstallError(RUNNING)
            try:
                self._installer.Commit(install.pending)
            finally:
                # A failed commit may have changed the folders (a partial
                # swap) and install.json.
                with self._lock:
                    self._records.pop(pluginId, None)
            self._AfterChange()
        except Exception as caught:
            error = caught
            if not isinstance(caught, InstallError):
                logger.opt(exception=True).debug("Plugin install: the "
                                                 "failure")
        finally:
            if held:
                self._jobs.Unhold(pluginId)
        self._Committed(install, error)

    def _Committed(self, install, error):
        with self._lock:
            self._installJobs = max(0, self._installJobs - 1)
            if error is None:
                install.state = InstallState.DONE
                # A failed removal of an earlier install is not this one's.
                self._removals.pop(install.pluginId, None)
                self._sizes.pop(install.pluginId, None)
            elif isinstance(error, InstallError) and error.code == RUNNING:
                # Kept pending by Commit: press again once the run ended.
                install.state = InstallState.CONSENT
                install.failure = Failure(error)
            else:
                install.state = InstallState.FAILED
                install.failure = Failure(error)
            state = install.state
        logger.info(f"Plugin install: {install.pluginId}: {state.value}")

    def Cancel(self, installId: str) -> InstallStatus:
        with self._lock:
            install = self._Find(installId)
            if install.state == InstallState.INSTALLING:
                raise PluginInstallsError(StartRefusal.BUSY,
                                          "the install is going")
            discard = install.pending \
                if install.state == InstallState.CONSENT else None
            if install.state in (InstallState.PREPARING,
                                 InstallState.CONSENT):
                install.state = InstallState.CANCELLED
        if discard is not None:
            self._installer.Discard(discard)
        with self._lock:
            return install.Status()

    # ── the enable ─────────────────────────────────────────────────────

    def SetEnabled(self, pluginId: str, enabled: bool) -> None:
        plugin = self._Installed(pluginId)
        with self._lock:
            busy = self._ChangingLocked(plugin.id)
            if busy is not None:
                raise PluginInstallsError(StartRefusal.BUSY, busy)
            self._enabling.add(plugin.id)
        held = False
        try:
            # A disable is held: a start planned before the write (it read
            # "enabled") is refused at its lane, one that has its lane is
            # cancelled.
            held = not enabled and self._Hold(plugin.id)
            try:
                self._installer.SetEnabled(plugin.id, bool(enabled))
            except OSError:
                raise PluginInstallsError(
                    StartRefusal.UNAVAILABLE,
                    "enabled.json not written") from None
            if held:
                self._jobs.CancelAll(plugin.id)
        finally:
            if held:
                self._jobs.Unhold(plugin.id)
            with self._lock:
                self._enabling.discard(plugin.id)
        if self._hooks is not None:
            try:
                self._hooks.Refresh()
            except Exception as error:
                logger.warning(f"Plugins: the hooks' refresh failed "
                               f"({type(error).__name__})")

    # ── a removal ──────────────────────────────────────────────────────

    def _Removable(self, pluginId):
        """The plugin a removal is about: an installed plugin of the list,
        or -- left the list after a partial removal -- an id with a failed
        removal (asked again / answered). (its id, whether it is listed)."""
        plugin = self._registry.Get(pluginId) \
            if isinstance(pluginId, str) else None
        if plugin is not None:
            if plugin.badge not in _INSTALLED_BADGES:
                raise PluginInstallsError(StartRefusal.INVALID_REQUEST,
                                          "not an installed plugin")
            return plugin.id, True
        with self._lock:
            known = isinstance(pluginId, str) and pluginId in self._removals
        if not known:
            raise PluginInstallsError(StartRefusal.NOT_FOUND,
                                      "no such plugin")
        return pluginId, False

    def _Sizes(self, pluginId):
        """RemovalSizes now, kept as the last ones."""
        try:
            sizes = tuple(self._installer.RemovalSizes(pluginId))
        except Exception as error:
            logger.warning(f"Plugins: the removal's sizes could not be read "
                           f"({type(error).__name__})")
            sizes = (0, 0, 0, 0, None)
        with self._lock:
            self._sizes[pluginId] = sizes
        return sizes

    @staticmethod
    def _Status(state, failure, sizes):
        data, runs, runsBytes, outBytes, work = sizes or (0, 0, 0, 0, None)
        return RemovalStatus(state=state, data_bytes=data, runs=runs,
                             runs_bytes=runsBytes, out_bytes=outBytes,
                             work_folder=work, failure=failure)

    def Removal(self, pluginId: str) -> RemovalStatus:
        pluginId, listed = self._Removable(pluginId)
        with self._lock:
            state, failure = self._removals.get(
                pluginId, (RemovalState.IDLE, None))
            last = self._sizes.get(pluginId)
        if state == RemovalState.REMOVING or (not listed and last):
            # Not walked while the job removes (the last sizes).
            return self._Status(state, failure, last)
        return self._Status(state, failure, self._Sizes(pluginId))

    def StartRemoval(self, pluginId: str, *, data: bool,
                     runs: str) -> RemovalStatus:
        if self._Stopping():
            raise PluginInstallsError(StartRefusal.UNAVAILABLE,
                                      "shutting down")
        pluginId, _listed = self._Removable(pluginId)
        with self._lock:
            last = self._sizes.get(pluginId)
        if last is None:
            last = self._Sizes(pluginId)
        with self._lock:
            busy = self._ChangingLocked(pluginId)
            if busy is not None:
                raise PluginInstallsError(StartRefusal.BUSY, busy)
            self._removals[pluginId] = (RemovalState.REMOVING, None)
        if not self._Submit(
                lambda: self._Remove(pluginId, data, runs),
                "plugin removal",
                lambda: self._Removed(pluginId, InstallError("stopped"))):
            self._Removed(pluginId, InstallError("stopped"))
        return self._Status(RemovalState.REMOVING, None, last)

    def _Remove(self, pluginId, data, runs):
        error = None
        held = False
        try:
            # Held first: neither a run nor a pending hook of it starts
            # while the running ones are cancelled and waited for, nor
            # until the scan has seen it gone.
            held = self._Hold(pluginId)
            if held:
                self._jobs.CancelAll(pluginId)
                # Also when none was asked: a run that ended may still be
                # writing its folder (Running counts it).
                self._WaitStopped(pluginId)
            try:
                self._installer.Remove(pluginId, data=data, runs=runs)
            except InstallError as caught:
                # Nothing left of it (a partial removal asked again): gone.
                if caught.code != NOT_FOUND:
                    raise
            finally:
                with self._lock:
                    self._records.pop(pluginId, None)
            self._AfterChange()
        except Exception as caught:
            error = caught
            if not isinstance(caught, InstallError):
                logger.opt(exception=True).debug("Plugin removal: the "
                                                 "failure")
            if not (isinstance(caught, InstallError)
                    and caught.code == RUNNING):
                # A partial removal changed the folders too.
                self._AfterChange()
        finally:
            if held:
                self._jobs.Unhold(pluginId)
        self._Removed(pluginId, error)

    def _WaitStopped(self, pluginId):
        deadline = self._clock() + REMOVAL_WAIT_SECS
        while self._jobs.Running(pluginId):
            if self._clock() >= deadline or self._Stopping():
                return
            self._sleep(_REMOVAL_POLL_SECS)

    def _Removed(self, pluginId, error):
        with self._lock:
            if error is None:
                self._removals.pop(pluginId, None)
                self._sizes.pop(pluginId, None)
            else:
                self._removals[pluginId] = (RemovalState.FAILED,
                                            Failure(error))
        logger.info(f"Plugin removal: {pluginId}: "
                    + ("done" if error is None else Failure(error).code))

    # ── the blocklist ──────────────────────────────────────────────────

    def Blocklist(self) -> Tuple[BlockEntry, ...]:
        with self._lock:
            return self._blocklist

    def BlockedReason(self, pluginId: str,
                      version: Optional[str]) -> Optional[Text]:
        blocklist = self.Blocklist()
        if not blocklist:
            return None
        with self._lock:
            known = pluginId in self._records
            record = self._records.get(pluginId)
        if not known:
            record = self._installer.ReadInstallRecord(pluginId)
            with self._lock:
                self._records[pluginId] = record
        entry = BlockedBy(pluginId, version or "",
                          record.sha256 if record else None,
                          record.source.repo_id if record else None,
                          blocklist)
        if entry is None:
            return None
        return entry.reason or dict(_BLOCKED_TEXT)

    def SetBlocklist(self, entries: Sequence[BlockEntry]) -> None:
        with self._lock:
            self._blocklist = tuple(entries)
        versions = {}
        try:
            for entry in self._registry.Entries():
                if entry.badge in _INSTALLED_BADGES:
                    versions[entry.id] = entry.version
        except Exception as error:
            logger.warning(f"Plugins: the list could not be read for the "
                           f"blocklist ({type(error).__name__})")
        blocked = frozenset(self._installer.BlockedInstalled(
            versions, self.Blocklist()))
        with self._lock:
            newly = sorted(blocked - self._blocked)
            self._blocked = blocked
        for pluginId in newly:
            logger.info(f"Plugins: {pluginId} is blocked")
            if self._Hold(pluginId):
                # As a disable: a start planned before the row was in
                # effect is refused at its lane.
                try:
                    self._jobs.CancelAll(pluginId)
                finally:
                    self._jobs.Unhold(pluginId)
            if self._hooks is not None:
                from .PluginTypes import HookDisabledReason
                try:
                    self._hooks.Forget(pluginId, HookDisabledReason.BLOCKED)
                except OSError as error:
                    logger.warning(f"Plugins: {pluginId}'s hooks could not "
                                   f"be turned off ({type(error).__name__})")
        if newly and self._hooks is not None:
            try:
                self._hooks.Refresh()
            except Exception as error:
                logger.warning(f"Plugins: the hooks' refresh failed "
                               f"({type(error).__name__})")
