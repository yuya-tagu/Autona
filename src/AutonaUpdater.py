"""
In-app auto-updater for Autona (download + external-helper install).

Flow:
    1. Check GitHub Releases API for the public yuya-tagu/Autona-releases repo
    2. Compare with local assets/versions.json
    3. If newer: show dialog -> user clicks "Download and install"
    4. Download release-windows.zip to %TEMP%/autona_update/update.zip
    5. UpdateProcedure() copies the bundled helper (assets/apply_update.ps1) to
       the temp workdir, shows a notice, then launches the helper as a
       hidden-console PowerShell process and exits the app.
    6. The helper waits for this process to fully exit (so every file unlocks),
       extracts the zip over the install dir (stripping the leading "Autona/"),
       relaunches the new exe, and cleans up.

Because the helper runs only after the app is gone, it can replace every file -
the launcher, the loaded _internal/ DLLs, and the memory-mapped WebEngine
resources - so there is no in-process extraction, no rename-aside sidecars, and
no Qt-version gate any more.

Only the frozen (PyInstaller) build can self-update. In dev mode
(python main.py) the check still works, but install is disabled and the dialog
explains why.
"""
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import zipfile
from pathlib import Path

import requests
from loguru import logger
from packaging.version import parse as parse_version
from qtpy.QtCore import *
from qtpy.QtGui import *
from qtpy.QtWidgets import *

from .Helpers.AutonaDirHelper import AutonaResolve
from .Helpers.AutonaRemoteZip import (
    RemoteZipError, RemoteZipReader, copy_unchanged_to_staging,
    diff_against_install, safe_dest, strip_root)
from .SettingsManager import SettingsManager


# Public distribution repo. The source repo (yuya-tagu/Autona) is private, so
# the unauthenticated GitHub Releases API the updater calls below would 404 for
# end users. Releases are published to this separate *public* repo instead, so
# anonymous clients can read /releases/latest and download the asset.
GITHUB_REPO = "yuya-tagu/Autona-releases"
RELEASE_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"
# Release asset produced by scripts/gen_release.ps1
ASSET_NAME = "release-windows.zip"

# Helper script bundled in assets/ (so gen_release.ps1 ships it inside the zip).
HELPER_SCRIPT = "apply_update.ps1"

# CreationFlags for the helper: hidden console + own process group (so the
# parent's shutdown signals don't reach it). Children outlive their parent on
# Windows by default, so no extra flag is needed for that.
# Do NOT add DETACHED_PROCESS: powershell.exe launched with no console at all
# exits immediately with code 0 without ever running the script (ConsoleHost
# fails to initialize), and CREATE_NO_WINDOW is ignored when combined with it.
# This exact combination silently broke the v1.0.7 -> v1.1.0 auto-update.
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000

# Legacy sidecar suffix left by the old in-process updater. Only referenced by
# CleanupUpdateLeftovers now, to purge stragglers when upgrading from a build
# that still used the rename-aside install path.
_LEGACY_RENAME_SUFFIX = ".autona_old"

# Lock file the external helper (apply_update.ps1) holds with an exclusive
# share mode for its entire run. While held, a freshly started app must not
# purge the workdir (CleanupUpdateLeftovers below) and refuses to start at all
# (gate in main.py — duplicated there because importing src boots Qt).
_UPDATE_LOCK_NAME = "update_in_progress.lock"

# Held by _DownloadThread while a diff download is building the staging dir
# (minutes, not seconds). Only CleanupUpdateLeftovers respects it — unlike
# _UPDATE_LOCK_NAME it must NOT block app startup (downloading in the
# background while the app runs is normal), so it is a separate file and the
# main.py gate does not know about it.
_DOWNLOAD_LOCK_NAME = "download_in_progress.lock"

# Written by the diff download after it fully assembles the staging dir.
# The helper checks it BEFORE the zip check and skips extraction; the
# updater checks it to skip _ExtractHelperFromZip (there is no zip). The
# name is a contract with apply_update.ps1 — pinned by
# tests/test_diff_update_contract.py.
_STAGING_MARKER_NAME = "staging_prebuilt.marker"

# Abort the diff path when the changed set exceeds this fraction of the
# archive (e.g. a Qt bump): one streamed full download beats holding a
# comparable volume of Range responses in memory.
_DIFF_MAX_FRACTION = 0.5


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def _install_dir() -> Path:
    """The directory the launcher lives in. For a onedir PyInstaller build
    sys.executable is ...\\Autona.exe, so its parent is the install root. This
    is more robust than Path.cwd() against a shortcut launching with a different
    working directory."""
    return Path(sys.executable).resolve().parent


def _update_workdir() -> Path:
    """A scratch dir outside the install tree for the zip, staging and helper.
    Keeping it out of the install dir means robocopy mirroring it back never
    races against the files the helper itself is reading."""
    base = Path(os.environ.get("TEMP") or os.environ.get("TMP")
                or r"C:\Windows\Temp")
    workdir = base / "autona_update"
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


def _local_version() -> str:
    try:
        with open(AutonaResolve("./assets/versions.json"), encoding="utf-8") as f:
            return str(json.load(f).get("program", "0"))
    except Exception:
        return "0"


def _is_newer(remote_tag: str, local_ver: str) -> bool:
    if not remote_tag:
        return False
    try:
        return parse_version(remote_tag.lstrip("v")) > parse_version(local_ver)
    except Exception:
        return remote_tag.lstrip("v") != local_ver


# ---------------------------------------------------------------------------
# UpdateProcedure (launch external helper) + leftover cleanup
# ---------------------------------------------------------------------------

def _generate_restart_messagebox(main_txt: str) -> QMessageBox:
    mb = QMessageBox()
    mb.setWindowTitle(QApplication.translate("app", "Warning"))
    mb.setText(
        main_txt + "\n" +
        QApplication.translate("app", "The program will now close.")
    )
    mb.finished.connect(QApplication.exit)
    return mb


def UpdateProcedure():
    """Launch the external update helper, then close the app.

    The helper waits for this process to exit, extracts update.zip over the
    install dir, and relaunches the new exe. Install is frozen-only; in dev the
    dialog already disables the install button before we get here.
    """
    try:
        if not _is_frozen():
            return
        # Last line of defense against spawning a second helper (the dialog
        # already gates the download; this covers any future caller).
        if _UpdateInFlight():
            logger.info("update helper already running; not spawning another")
            return

        install_dir = _install_dir()
        exe_name = Path(sys.executable).name          # Autona.exe
        workdir = _update_workdir()
        zip_path = workdir / "update.zip"
        staging = workdir / "staging"
        script = workdir / HELPER_SCRIPT

        # Run the helper that ships INSIDE the downloaded payload, not the
        # one in the install dir. The installed copy is by definition one
        # release old, so a helper bugfix could otherwise never apply to the
        # very update that delivers it (v1.3.0's guard-less helper had to
        # install v1.3.1 and none of v1.3.1's fixes protected that run).
        # Same principle as Squirrel/Omaha: update logic comes from the
        # payload. A diff download has no zip — it already wrote both the
        # payload helper and the staging marker to the workdir. Fall back to
        # the installed copy so a zip without the helper still updates.
        marker = workdir / _STAGING_MARKER_NAME
        if marker.exists() and script.exists():
            logger.info("diff download prepared the staging and helper")
        elif not _ExtractHelperFromZip(zip_path, script):
            logger.warning(
                f"{HELPER_SCRIPT} not found in update.zip; "
                "falling back to the installed copy")
            shutil.copyfile(AutonaResolve(f"./assets/{HELPER_SCRIPT}"), script)

        # Tell the user, then close. finished -> QApplication.exit (connected in
        # _generate_restart_messagebox). We spawn the helper after exec() so the
        # user has acknowledged before the app starts tearing down.
        mb = _generate_restart_messagebox(
            QApplication.translate(
                "updater",
                "Update downloaded. The app will close, install the update, "
                "and restart automatically.")
            + "\n"
        )
        mb.exec()

        _LaunchHelper(script, zip_path, install_dir, staging, exe_name)
        QApplication.exit()
    except Exception as e:
        logger.error(traceback.format_exc())

        retry = QDialog()
        retry.setWindowTitle(QApplication.translate("app", "Warning"))
        vbox = QVBoxLayout()
        retry.setLayout(vbox)
        vbox.addWidget(QLabel(
            QApplication.translate("updater", "Error while preparing the update:")))
        vbox.addWidget(QLabel(str(e)))

        hbox = QHBoxLayout()
        vbox.addLayout(hbox)
        btRetry = QPushButton(QApplication.translate("updater", "Retry"))
        hbox.addWidget(btRetry)
        btCancel = QPushButton(QApplication.translate("updater", "Cancel"))
        hbox.addWidget(btCancel)

        btRetry.clicked.connect(lambda: [retry.close(), UpdateProcedure()])
        btCancel.clicked.connect(lambda: retry.close())
        retry.exec()


def _ExtractHelperFromZip(zip_path: Path, dest: Path) -> bool:
    """Pull assets/apply_update.ps1 out of the downloaded release zip.

    Tolerant of the leading folder name and of '\\' separators (a malformed
    zip already burned one release), since this runs before the helper's own
    normalization exists on disk."""
    suffix = f"/assets/{HELPER_SCRIPT}".lower()
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                name = info.filename.replace("\\", "/").lower()
                if name.endswith(suffix):
                    with zf.open(info) as src, open(dest, "wb") as out:
                        shutil.copyfileobj(src, out)
                    return True
    except Exception:
        logger.error(traceback.format_exc())
    return False


def _LaunchHelper(script: Path, zip_path: Path, install_dir: Path,
                  staging: Path, exe_name: str) -> None:
    """Spawn apply_update.ps1 as a hidden-console PowerShell process that
    outlives this app. shell=False with an argument list keeps spaces/non-ASCII
    paths safe; powershell.exe is referenced by full path so it resolves outside
    the install dir (which is about to be mirrored over)."""
    powershell = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"),
        "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    args = [
        powershell, "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-WindowStyle", "Hidden", "-File", str(script),
        "-ParentPid", str(os.getpid()),
        "-Zip", str(zip_path),
        "-InstallDir", str(install_dir),
        "-Staging", str(staging),
        "-ExeName", exe_name,
    ]
    subprocess.Popen(
        args,
        cwd=str(_update_workdir()),  # not the install dir, so we don't lock it
        creationflags=(_CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW),
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _LockHeld(lock: Path) -> bool:
    """True while someone holds `lock` open without delete-sharing.

    The helper opens its lock with FileShare.None and Python's open() gives
    no FILE_SHARE_DELETE either, so unlink raises a sharing violation for as
    long as the owner lives. A lock file that CAN be deleted is a stale
    leftover from a crashed owner — remove it and report not held."""
    if not lock.exists():
        return False
    try:
        lock.unlink()
        return False
    except OSError:
        return True


def _UpdateInFlight() -> bool:
    """True while the external helper (apply_update.ps1) is installing."""
    return _LockHeld(_update_workdir() / _UPDATE_LOCK_NAME)


def _DownloadInFlight() -> bool:
    """True while a diff download is assembling the staging dir."""
    return _LockHeld(_update_workdir() / _DOWNLOAD_LOCK_NAME)


def CleanupUpdateLeftovers():
    """Purge stragglers from older update runs and the temp workdir.

    Runs once at startup, frozen builds only. Removes the temp workdir from the
    current external-helper flow (in case the helper's self-delete didn't fire),
    plus leftovers from the previous in-process updater that a user may carry
    over when upgrading: the legacy ./Autona_old.exe and any *.autona_old
    sidecars in the root and _internal/."""
    if not _is_frozen():
        return
    try:
        # Never purge under a live update: deleting the helper's staging dir
        # mid-robocopy loses the update (v1.3.0 E2E, robocopy exit 11). The
        # main.py gate normally prevents us from running at all in this state;
        # this covers the launch-before-lock race.
        if _UpdateInFlight():
            logger.info("update helper is running; skipping leftover cleanup")
            return
        # Same protection for a diff download building the staging dir from
        # another instance — the window is minutes long, so this race is not
        # theoretical (a second instance's startup cleanup deleting a live
        # workdir is exactly the v1.3.0->v1.3.1 failure shape).
        if _DownloadInFlight():
            logger.info("diff download in progress; skipping leftover cleanup")
            return
        # Temp workdir from the external helper (zip/staging/script). The helper
        # self-deletes its folder, but clean it here as a backstop.
        try:
            workdir = _update_workdir()
            if workdir.exists():
                shutil.rmtree(workdir, ignore_errors=True)
        except Exception:
            pass

        # Legacy onefile backup name.
        if os.path.exists("./Autona_old.exe"):
            try:
                os.remove("./Autona_old.exe")
            except Exception:
                pass

        # Legacy in-process sidecars: the launcher's sat at the root, the bulk
        # under _internal/. Walk just those to avoid scanning game assets.
        roots = [Path("."), Path("./_internal")]
        seen = set()
        for root in roots:
            if not root.exists():
                continue
            # Path(".") walk would recurse into _internal twice; dedupe files.
            pattern = ("*" + _LEGACY_RENAME_SUFFIX) if root == Path(".") \
                else ("**/*" + _LEGACY_RENAME_SUFFIX)
            for sidecar in root.glob(pattern):
                rp = sidecar.resolve()
                if rp in seen:
                    continue
                seen.add(rp)
                try:
                    sidecar.unlink()
                except Exception:
                    pass
    except Exception:
        logger.error(traceback.format_exc())


# ---------------------------------------------------------------------------
# Background threads
# ---------------------------------------------------------------------------

class _CheckThread(QThread):
    # (current_version, latest_tag, release_url, asset_url, body)
    newVersion = Signal(str, str, str, str, str)
    noUpdate = Signal()
    error = Signal(str)

    def run(self):
        try:
            resp = requests.get(RELEASE_API_URL, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            tag = (data.get("tag_name") or "").strip()
            release_url = data.get("html_url") or RELEASES_PAGE_URL
            body = data.get("body") or ""
            asset_url = ""
            for asset in data.get("assets", []):
                if asset.get("name") == ASSET_NAME:
                    asset_url = asset.get("browser_download_url", "")
                    break

            current = _local_version()
            if _is_newer(tag, current):
                self.newVersion.emit(
                    current, tag.lstrip("v"), release_url, asset_url, body)
            else:
                self.noUpdate.emit()
        except Exception as e:
            logger.error("update check failed: " + traceback.format_exc())
            self.error.emit(str(e))


class _DiffCancelled(Exception):
    """User cancelled mid-diff: abort the update, do NOT fall back to the
    full download."""


class _DownloadThread(QThread):
    progress = Signal(int, int)  # received, total (total=0 if unknown)
    status = Signal(str)         # phase description for the status label
    done = Signal()
    error = Signal(str)

    def __init__(self, url, dest: Path):
        super().__init__()
        self.url = url
        self.dest = dest
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        # Try the differential path first: fetch only the entries whose CRC
        # differs from the local install (~6% of the archive on a typical
        # release, measured v1.3.0->v1.3.2: 19.7 of 310.6 MB). ANY problem
        # falls through to the full download below — diff is an optimization
        # and must never add a failure mode.
        try:
            if self._TryDiffDownload():
                return
        except _DiffCancelled:
            self._CleanupDiffLeftovers()
            self.error.emit("cancelled")
            return
        self._FullDownload()

    # ------------------------------------------------------------------
    # Differential download
    # ------------------------------------------------------------------

    def _CleanupDiffLeftovers(self):
        """Remove every artifact a failed/cancelled diff may have left, so
        the full-download path (or the next attempt) starts clean and the
        helper can never see a marker next to a half-built staging."""
        workdir = _update_workdir()
        for leftover in (workdir / _STAGING_MARKER_NAME,
                         workdir / HELPER_SCRIPT):
            try:
                if leftover.exists():
                    leftover.unlink()
            except OSError:
                pass
        try:
            staging = workdir / "staging"
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
        except Exception:
            pass

    def _TryDiffDownload(self) -> bool:
        """Returns True when the diff path fully handled the request (done
        emitted). False → caller runs the full download. Raises
        _DiffCancelled on user cancel."""
        if not _is_frozen():
            return False  # no install dir to diff against under python main.py

        workdir = _update_workdir()
        staging = workdir / "staging"
        marker = workdir / _STAGING_MARKER_NAME
        install_dir = _install_dir()
        lock = None
        try:
            # Order matters: write the marker LAST, remove it FIRST.
            self._CleanupDiffLeftovers()

            # Held for the whole build (minutes). CleanupUpdateLeftovers in
            # another instance skips the workdir purge while this lives.
            lock = open(workdir / _DOWNLOAD_LOCK_NAME, "wb")

            self.status.emit(QApplication.translate(
                "updater", "Comparing files with the new version..."))
            reader = RemoteZipReader(self.url)
            reader.load_directory()

            def cancelled():
                return self._cancel

            plan = diff_against_install(reader.entries, install_dir,
                                        cancel_cb=cancelled)

            # The helper must come from the payload even when unchanged.
            helper_rel = f"assets/{HELPER_SCRIPT}"
            wanted = list(plan.changed)
            if not any(strip_root(e.name) == helper_rel for e in wanted):
                helper_entry = next(
                    (e for e in reader.entries
                     if strip_root(e.name) == helper_rel), None)
                if helper_entry is None:
                    raise RemoteZipError("payload has no update helper")
                wanted.append(helper_entry)

            if plan.download_comp > plan.total_comp * _DIFF_MAX_FRACTION:
                raise RemoteZipError(
                    f"changed set too large for diff "
                    f"({plan.download_comp / (1024*1024):.0f} MB of "
                    f"{plan.total_comp / (1024*1024):.0f} MB)")
            free = shutil.disk_usage(workdir).free
            if free < plan.total_file_size + 512 * 1024 * 1024:
                raise RemoteZipError(
                    f"not enough temp space for staging "
                    f"({free / (1024*1024):.0f} MB free)")

            self.status.emit(QApplication.translate("updater", "Downloading..."))
            total_dl = sum(e.comp_size for e in wanted)
            helper_bytes = []

            def sink(entry, data):
                rel = strip_root(entry.name)
                if rel == helper_rel:
                    helper_bytes.append(data)
                dest = safe_dest(staging, rel)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)

            reader.fetch_entries(
                wanted, sink,
                progress_cb=lambda comp: self.progress.emit(comp, total_dl),
                cancel_cb=cancelled)
            if self._cancel:
                raise _DiffCancelled()

            # The helper that will install this staging must understand the
            # marker protocol. An older helper would fail loudly on the
            # missing zip — recoverable, but the full download just works.
            if not helper_bytes or \
                    _STAGING_MARKER_NAME.encode("ascii") not in helper_bytes[0]:
                raise RemoteZipError(
                    "payload helper does not support prebuilt staging")

            copy_unchanged_to_staging(staging, install_dir, plan.unchanged,
                                      cancel_cb=cancelled)

            (workdir / HELPER_SCRIPT).write_bytes(helper_bytes[0])
            marker.write_text(json.dumps({
                "changed": len(plan.changed),
                "unchanged": len(plan.unchanged),
                "downloaded_bytes": total_dl,
            }), encoding="utf-8")

            logger.info(
                f"diff download ok: {len(plan.changed)} changed + "
                f"{len(plan.unchanged)} reused entries, "
                f"{total_dl / (1024*1024):.1f} of "
                f"{plan.total_comp / (1024*1024):.1f} MB fetched")
            self.progress.emit(total_dl, total_dl)
            self.done.emit()
            return True
        except _DiffCancelled:
            raise
        except RemoteZipError as e:
            if self._cancel:
                raise _DiffCancelled() from None
            logger.warning(f"diff download unavailable: {e}; "
                           "falling back to the full download")
            self._CleanupDiffLeftovers()
            return False
        except Exception:
            if self._cancel:
                raise _DiffCancelled() from None
            logger.warning("diff download failed; falling back to the full "
                           "download\n" + traceback.format_exc())
            self._CleanupDiffLeftovers()
            return False
        finally:
            if lock is not None:
                try:
                    lock.close()
                    (workdir / _DOWNLOAD_LOCK_NAME).unlink()
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Full download (pre-diff behavior, also the fallback)
    # ------------------------------------------------------------------

    def _FullDownload(self):
        # After a failed diff attempt the label may still read "Comparing
        # files..." — reset it to the plain download phase.
        self.status.emit(QApplication.translate("updater", "Downloading..."))
        # Declare tmp outside the try so the except block can safely
        # reference it even if .with_suffix() somehow raises.
        tmp = self.dest.with_suffix(self.dest.suffix + ".part")
        try:
            with requests.get(self.url, stream=True, timeout=30) as r:
                r.raise_for_status()
                total = int(r.headers.get("Content-Length", 0))
                received = 0
                last_emit = 0.0
                with open(tmp, "wb") as f:
                    # 1 MB chunks keep per-chunk Python overhead negligible, and
                    # the progress signal is throttled to ~10/s. Previously this
                    # emitted a cross-thread signal every 64 KB (~6000 times for
                    # a 384 MB release), flooding the GUI thread and noticeably
                    # slowing the download.
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if self._cancel:
                            raise RuntimeError("cancelled")
                        if not chunk:
                            continue
                        f.write(chunk)
                        received += len(chunk)
                        now = time.monotonic()
                        if now - last_emit >= 0.1:
                            last_emit = now
                            self.progress.emit(received, total)
                # Final emit so the bar lands on 100%.
                self.progress.emit(received, total)
            os.replace(tmp, self.dest)
            self.done.emit()
        except Exception as e:
            logger.error("download failed: " + traceback.format_exc())
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass
            self.error.emit(str(e))


# ---------------------------------------------------------------------------
# Update dialog
# ---------------------------------------------------------------------------

class _UpdateDialog(QDialog):
    def __init__(self, current, latest, release_url, asset_url, body, parent=None):
        super().__init__(parent)
        # Release the dialog's C++ object and Python refs when the user
        # closes it, so repeated manual checks don't leak dialog instances.
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle(
            QApplication.translate("updater", "A new version is available"))
        self.setMinimumWidth(520)

        self._current = current
        self._latest = latest
        self._asset_url = asset_url
        self._release_url = release_url
        self._thread = None

        layout = QVBoxLayout(self)

        title = QLabel(
            QApplication.translate(
                "updater",
                "A new version v{0} is available (current v{1})."
            ).format(latest, current)
        )
        title.setStyleSheet("font-weight: bold; font-size: 12pt;")
        layout.addWidget(title)

        link = QLabel(
            f'<a href="{release_url}">' +
            QApplication.translate("updater", "Open the release page") +
            '</a>'
        )
        link.setOpenExternalLinks(True)
        layout.addWidget(link)

        if body:
            notes = QTextEdit()
            notes.setReadOnly(True)
            notes.setPlainText(body)
            notes.setMinimumHeight(120)
            notes.setMaximumHeight(220)
            layout.addWidget(notes)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.statusLabel = QLabel("")
        self.statusLabel.setWordWrap(True)
        self.statusLabel.setVisible(False)
        layout.addWidget(self.statusLabel)

        btns = QHBoxLayout()
        layout.addLayout(btns)

        self.btInstall = QPushButton(
            QApplication.translate("updater", "Download and install"))
        btns.addWidget(self.btInstall)
        self.btInstall.clicked.connect(self._StartDownload)

        btns.addStretch(1)

        self.btSkip = QPushButton(
            QApplication.translate("updater", "Skip this version"))
        btns.addWidget(self.btSkip)
        self.btSkip.clicked.connect(self._Skip)

        self.btClose = QPushButton(
            QApplication.translate("updater", "Later"))
        btns.addWidget(self.btClose)
        self.btClose.clicked.connect(self.close)

        if not asset_url:
            self.btInstall.setEnabled(False)
            self.statusLabel.setText(
                QApplication.translate(
                    "updater",
                    "{0} is not attached to this release. "
                    "Please download it manually from the release page."
                ).format(ASSET_NAME)
            )
            self.statusLabel.setVisible(True)
        elif not _is_frozen():
            # In dev (python main.py) there is no Autona.exe to replace.
            self.btInstall.setEnabled(False)
            self.statusLabel.setText(
                QApplication.translate(
                    "updater",
                    "Auto-install is not available in dev mode "
                    "(python main.py). Only the distributed exe can self-update."
                )
            )
            self.statusLabel.setVisible(True)

    def _StartDownload(self):
        # A helper from an earlier attempt may still be installing (this app
        # can be an instance that was already open when it started), or
        # another instance may be mid-download. Both share this workdir, so
        # proceeding would clobber the zip/staging they are working from —
        # the concurrent-run mess behind the v1.3.0 -> v1.3.1 failures
        # (update_log 2026-07-16).
        if _UpdateInFlight() or _DownloadInFlight():
            self.statusLabel.setText(
                QApplication.translate(
                    "updater",
                    "An update is already being installed. Close Autona and "
                    "it will restart automatically when the install finishes."))
            self.statusLabel.setVisible(True)
            return

        # Download into the temp workdir (outside the install dir) where the
        # helper expects update.zip.
        dest = _update_workdir() / "update.zip"

        self.btInstall.setEnabled(False)
        self.btSkip.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.statusLabel.setText(
            QApplication.translate("updater", "Downloading..."))
        self.statusLabel.setVisible(True)

        self._thread = _DownloadThread(self._asset_url, dest)
        self._thread.progress.connect(self._OnProgress)
        self._thread.status.connect(self._OnStatus)
        self._thread.done.connect(self._OnDownloadDone)
        self._thread.error.connect(self._OnError)
        self._thread.start()

    def _OnStatus(self, text):
        self.statusLabel.setText(text)
        self.statusLabel.setVisible(True)

    def _OnProgress(self, received, total):
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(received)
            mb_r = received / (1024 * 1024)
            mb_t = total / (1024 * 1024)
            self.statusLabel.setText(
                QApplication.translate(
                    "updater", "Downloading... {0:.1f} / {1:.1f} MB"
                ).format(mb_r, mb_t)
            )
        else:
            self.progress.setRange(0, 0)

    def _OnDownloadDone(self):
        self.progress.setVisible(False)
        self.statusLabel.setText(
            QApplication.translate(
                "updater", "Download complete. Preparing install..."))
        # Close this dialog before UpdateProcedure's modal message box.
        self.accept()
        # Hand off to the original install flow.
        UpdateProcedure()

    def _OnError(self, msg):
        self.progress.setVisible(False)
        self.btInstall.setEnabled(True)
        self.btSkip.setEnabled(True)
        self.statusLabel.setText(
            QApplication.translate(
                "updater", "Download failed: {0}"
            ).format(msg)
        )

    def _Skip(self):
        SettingsManager.Set("update.skipped_version", self._latest)
        self.close()

    def closeEvent(self, event):
        if self._thread and self._thread.isRunning():
            self._thread.cancel()
            self._thread.wait(3000)
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Public facade
# ---------------------------------------------------------------------------

class AutonaUpdater(QObject):
    instance: "AutonaUpdater" = None

    def __init__(self):
        super().__init__()
        self._check_thread = None
        self._silent = False
        self._dialog = None

    def CheckForUpdates(self, silent: bool = False):
        """
        silent=True  -> suppress "up to date" / error dialogs (startup)
        silent=False -> always show a result dialog (manual menu check)
        """
        if self._check_thread is not None and self._check_thread.isRunning():
            return

        self._silent = silent
        self._check_thread = _CheckThread()
        self._check_thread.newVersion.connect(self._OnNewVersion)
        self._check_thread.noUpdate.connect(self._OnNoUpdate)
        self._check_thread.error.connect(self._OnError)
        self._check_thread.start()

    def _OnNewVersion(self, current, latest, release_url, asset_url, body):
        # Respect "skip this version" for startup (silent) checks only.
        if self._silent:
            skipped = SettingsManager.Get("update.skipped_version", "")
            if skipped and skipped == latest:
                logger.info(f"update v{latest} skipped by user setting")
                return

        # No Qt-version gate any more: the external helper installs after the
        # app has fully exited, so even the memory-mapped WebEngine resources
        # are free to replace. Any release - including a Qt bump - applies
        # cleanly.
        self._dialog = _UpdateDialog(current, latest, release_url, asset_url, body)
        self._dialog.show()

    def _OnNoUpdate(self):
        if self._silent:
            return
        QMessageBox.information(
            None,
            QApplication.translate("updater", "Check for updates"),
            QApplication.translate("updater", "You are on the latest version."),
        )

    def _OnError(self, msg):
        if self._silent:
            return
        QMessageBox.warning(
            None,
            QApplication.translate("updater", "Check for updates"),
            QApplication.translate(
                "updater", "Update check failed:\n{0}"
            ).format(msg),
        )


AutonaUpdater.instance = AutonaUpdater()
