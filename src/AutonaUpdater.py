"""
In-app auto-updater for Autona (download + external-helper install).

Flow:
    1. Check GitHub Releases API for the public yuya-tagu/Autona-releases repo
    2. Compare with local assets/versions.json
    3. If newer: show dialog -> user clicks "Download and install"
    4. Download release-windows.zip to %TEMP%/autona_update/update.zip
    5. UpdateProcedure() copies the bundled helper (assets/apply_update.ps1) to
       the temp workdir, shows a notice, then launches the helper as a detached
       PowerShell process and exits the app.
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
from pathlib import Path

import requests
from loguru import logger
from packaging.version import parse as parse_version
from qtpy.QtCore import *
from qtpy.QtGui import *
from qtpy.QtWidgets import *

from .Helpers.AutonaDirHelper import AutonaResolve
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

# CreationFlags for the detached helper: survive the parent's exit, no console
# window, own process group (so the parent's shutdown signals don't reach it).
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000

# Legacy sidecar suffix left by the old in-process updater. Only referenced by
# CleanupUpdateLeftovers now, to purge stragglers when upgrading from a build
# that still used the rename-aside install path.
_LEGACY_RENAME_SUFFIX = ".autona_old"


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

        install_dir = _install_dir()
        exe_name = Path(sys.executable).name          # Autona.exe
        workdir = _update_workdir()
        zip_path = workdir / "update.zip"
        staging = workdir / "staging"
        script = workdir / HELPER_SCRIPT

        # Copy the bundled helper out to the temp workdir so it isn't itself a
        # target of the robocopy mirror back into the install dir.
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


def _LaunchHelper(script: Path, zip_path: Path, install_dir: Path,
                  staging: Path, exe_name: str) -> None:
    """Spawn apply_update.ps1 as a detached, windowless PowerShell process that
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
        creationflags=(_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP
                       | _CREATE_NO_WINDOW),
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


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


class _DownloadThread(QThread):
    progress = Signal(int, int)  # received, total (total=0 if unknown)
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
        self._thread.done.connect(self._OnDownloadDone)
        self._thread.error.connect(self._OnError)
        self._thread.start()

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
