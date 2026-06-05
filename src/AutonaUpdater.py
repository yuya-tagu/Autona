"""
In-app auto-updater for Autona (Option D: download + restart-to-install).

Design (respects the original TSH auto-update flow):
    1. Check GitHub Releases API for yuya-tagu/Autona
    2. Compare with local assets/versions.json
    3. If newer: show dialog -> user clicks Download
    4. Download release-windows.zip to ./update.zip
    5. UpdateProcedure() backs up ./layout and registers ExtractUpdate via atexit
    6. App exits -> ExtractUpdate() renames Autona.exe -> Autona_old.exe
       and extracts the zip (stripping the leading "Autona/" folder)
    7. User re-launches Autona.exe; startup code removes Autona_old.exe

Only the frozen (PyInstaller) build can actually replace Autona.exe. In
dev mode (python main.py) the check still works, but install is disabled
and the dialog explains why.
"""
import atexit
import json
import os
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
# Fixed name expected by ExtractUpdate (matches original TSH behaviour)
UPDATE_ZIP = "./update.zip"

# Guards ExtractUpdate from being registered via atexit twice when the
# UpdateProcedure retry flow re-enters (see original TSH behaviour).
_extract_registered = False


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


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
# UpdateProcedure / ExtractUpdate
# ---------------------------------------------------------------------------
#
# Moved here from Autona.py so the whole update flow lives in one module.
# ExtractUpdate is a fix of the original: `z.extract(filename, path=dirname)`
# double-nested the output (layout/Autona/layout/foo.xml). We now read the
# entry manually and write it to the correct stripped path.

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
    """Back up the layout folder and register ExtractUpdate to run at exit."""
    global _extract_registered
    try:
        if os.path.exists("./layout"):
            os.rename("./layout", f"./layout_backup_{str(time.time())}")

        if not _extract_registered:
            atexit.register(ExtractUpdate)
            _extract_registered = True

        mb = _generate_restart_messagebox(
            QApplication.translate(
                "app",
                "Update download complete. The program will extract the update upon closing.")
            + "\n\n"
            + QApplication.translate(
                "app",
                "Please ensure the layout folder or its contents aren't open "
                "in another application before closing this window.")
            + "\n"
        )
        mb.exec()
    except Exception as e:
        logger.error(traceback.format_exc())

        retry = QDialog()
        retry.setWindowTitle(QApplication.translate("app", "Warning"))
        vbox = QVBoxLayout()
        retry.setLayout(vbox)
        vbox.addWidget(QLabel(
            QApplication.translate("updater", "Error while backing up the layout folder:")))
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


def ExtractUpdate():
    """Replace the installed files from UPDATE_ZIP on app exit.

    The release zip produced by gen_release.ps1 has entries like
        Autona/
        Autona/Autona.exe
        Autona/assets/...
    We strip the leading "Autona/" segment and write files relative to CWD.
    """
    try:
        update_log = []

        with zipfile.ZipFile(UPDATE_ZIP, "r") as z:
            # Back up the running exe; next launch cleans up Autona_old.exe.
            if os.path.exists("./Autona.exe"):
                try:
                    if os.path.exists("./Autona_old.exe"):
                        os.remove("./Autona_old.exe")
                    os.rename("./Autona.exe", "./Autona_old.exe")
                except Exception:
                    update_log.append(
                        "Failed to back up Autona.exe - " + traceback.format_exc())

            install_root = Path.cwd().resolve()

            for entry in z.namelist():
                # Skip anything that doesn't live under the top-level folder.
                if "/" not in entry:
                    continue
                relative = entry.split("/", 1)[1]
                if not relative:
                    continue

                # ZIP slip guard: reject entries with .. or absolute paths
                # that would escape the install directory.
                rel_path = Path(relative)
                if rel_path.is_absolute() or ".." in rel_path.parts:
                    update_log.append(
                        f"Rejected unsafe path {entry!r} -> {relative!r}")
                    continue
                try:
                    resolved = (install_root / rel_path).resolve()
                    resolved.relative_to(install_root)
                except (ValueError, OSError):
                    update_log.append(
                        f"Rejected unsafe path {entry!r} -> {relative!r}")
                    continue

                target = Path(relative)
                if entry.endswith("/"):
                    update_log.append(f"Create directory {relative}")
                    try:
                        target.mkdir(parents=True, exist_ok=True)
                    except Exception:
                        update_log.append(
                            f"Failed to mkdir {relative} - {traceback.format_exc()}")
                    continue

                update_log.append(f"Extract {entry} -> {relative}")
                try:
                    if target.parent and str(target.parent) not in ("", "."):
                        target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(entry) as src, open(target, "wb") as dst:
                        while True:
                            chunk = src.read(1024 * 1024)
                            if not chunk:
                                break
                            dst.write(chunk)
                except Exception:
                    update_log.append(
                        f"Failed to extract {entry} - {traceback.format_exc()}")

            try:
                os.makedirs("assets", exist_ok=True)
                with open("assets/update_log.txt", "w", encoding="utf-8") as f:
                    f.write("\n".join(update_log))
            except Exception:
                logger.error(traceback.format_exc())

        try:
            os.remove(UPDATE_ZIP)
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
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        if self._cancel:
                            raise RuntimeError("cancelled")
                        if not chunk:
                            continue
                        f.write(chunk)
                        received += len(chunk)
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
        dest = Path(UPDATE_ZIP)

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
