"""Dialog for adding game assets.

Autona does not bundle character/stage images because they are copyrighted by
their respective rights holders (see user_data/games/README.md). This window
lets the operator obtain them WITHOUT installing TournamentStreamHelper:

  * Online: download asset packs directly from the public upstream source that
    TSH itself uses — joaorb64/StreamHelperAssets.
  * Import: copy an already-downloaded game folder / ZIP (offline fallback).

The work is core's (ADR-005 S-final SF-3b): core/AssetPackService fetches the
index, downloads and extracts the packs, and imports a folder or a ZIP as jobs
of the TaskExecutor, then re-scans the games with the shell's scan (the
adapter's: onLoadAssets -> Window.ReloadGames(), so the game appears in the
selector live, no restart). This window asks for them and shows how they go,
polling the service on a timer (a Qt widget does not subscribe to core's
channels: tests/test_signal_lifetime.py); the file dialogs and the overwrite
questions stay here. The React page that replaces it (SF-4b) does the same
through /api/v1/assets*.

Autona itself neither bundles nor hosts these assets — they are fetched on
demand from the upstream public source at the operator's request (same posture
as TSH). User-facing strings go through QApplication.translate("app", ...);
Japanese strings live in Autona_ja.ts.
"""

import os

from loguru import logger

from qtpy.QtCore import *
from qtpy.QtGui import *
from qtpy.QtWidgets import *

from . import ui_constants as UI
from .core import AssetPackService as assets
from .core.AssetPackService import AssetPackService

# How often the window reads the index fetch's and its job's progress.
_POLL_MS = 100


def _tr(text):
    return QApplication.translate("app", text)


class AutonaGameAssetGuideWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        # code -> the game's row of AssetPackService.Packs() (the index's
        # games only: what can be downloaded).
        self._games = {}
        self._indexFuture = None
        # The job this window started: its revision, "download" or
        # "import", and the progress dialog of a download.
        self._jobRevision = None
        self._jobKind = None
        self._progressDialog = None
        self._label = ""
        self._polling = False
        self._poll = QTimer(self)
        self._poll.setInterval(_POLL_MS)
        self._poll.timeout.connect(self._onPoll)

    # Built lazily once the app is fully up, mirroring the other guide windows.
    def UiMounted(self):
        self.setWindowTitle(_tr("Add game assets"))
        self.resize(660, 0)

        layout = QVBoxLayout(self)

        desc = QLabel(_tr(
            "Game assets (character / stage images) are copyrighted by their "
            "respective rights holders and are not bundled with Autona. Download "
            "them from the online source below — no TournamentStreamHelper "
            "install needed — or import a folder / ZIP you already have."))
        desc.setWordWrap(True)
        layout.addWidget(desc)

        layout.addWidget(self._buildOnlineGroup())
        layout.addWidget(self._buildImportGroup())
        layout.addLayout(self._buildSourceRow())
        layout.addStretch(1)

        # Kick off the index fetch so the game list is ready by the time the
        # operator looks at it (a fresh cached one is used as it is).
        self._fetchIndex(refresh=False)

    # ── Online download ──────────────────────────────────────────────────
    def _buildOnlineGroup(self):
        group = QGroupBox(_tr("Download online"))
        v = QVBoxLayout()

        self._onlineStatus = QLabel(_tr("Loading game list…"))
        self._onlineStatus.setWordWrap(True)
        v.addWidget(self._onlineStatus)

        row = QHBoxLayout()
        row.addWidget(QLabel(_tr("Game") + ":"))
        self._gameCombo = QComboBox()
        self._gameCombo.setEditable(True)
        self._gameCombo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self._gameCombo.completer().setCompletionMode(QCompleter.PopupCompletion)
        self._gameCombo.setEnabled(False)
        self._gameCombo.activated.connect(self._rebuildPackChecks)
        row.addWidget(self._gameCombo, 1)
        v.addLayout(row)

        # Pack checkboxes, rebuilt when the selected game changes.
        self._packsBox = QGroupBox(_tr("Packs"))
        self._packsLayout = QVBoxLayout(self._packsBox)
        self._packChecks = []
        v.addWidget(self._packsBox)

        note = QLabel(_tr(
            "Note: official illustration / render / promo / audio packs are not "
            "offered, per the game publisher's tournament guidelines."))
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {UI.palette()['text_label']};")
        v.addWidget(note)

        btnRow = QHBoxLayout()
        self._btDownload = QPushButton(_tr("Download && load"))
        self._btDownload.setEnabled(False)
        self._btDownload.clicked.connect(self._startDownload)
        btnRow.addWidget(self._btDownload)

        self._btRetryIndex = QPushButton(_tr("Reload game list"))
        self._btRetryIndex.clicked.connect(
            lambda *_: self._fetchIndex(refresh=True))
        btnRow.addWidget(self._btRetryIndex)
        btnRow.addStretch(1)
        v.addLayout(btnRow)

        group.setLayout(v)
        return group

    def _fetchIndex(self, refresh=True):
        if self._indexFuture is not None and not self._indexFuture.done():
            return
        self._onlineStatus.setText(_tr("Loading game list…"))
        self._gameCombo.setEnabled(False)
        self._btDownload.setEnabled(False)
        self._indexFuture = AssetPackService.instance.FetchIndex(refresh)
        self._poll.start()

    def _onIndexDone(self, future):
        self._indexFuture = None
        if future.cancelled():
            return  # the app is shutting down
        error = None
        try:
            cached = future.result()
        except Exception as failure:
            logger.warning(f"Asset index fetch failed: {failure}")
            cached, error = False, str(failure)
        if error is None:
            error = AssetPackService.instance.IndexState()["error"]
        if cached:
            self._onIndexLoaded()
        if error is not None or not cached:
            # A refresh that failed over an older list keeps the list.
            self._onIndexFailed(error or "")

    def _onIndexLoaded(self):
        payload = AssetPackService.instance.Packs(fetch=False)
        self._games = {game["code"]: game for game in payload["games"]
                       if game["available"]}
        self._gameCombo.clear()
        # In the service's order (display name); data = game code (folder
        # name).
        for code, game in self._games.items():
            self._gameCombo.addItem(f"{game['name']}  ({code})", code)
        self._gameCombo.setEnabled(True)
        self._btDownload.setEnabled(
            self._gameCombo.count() > 0 and self._jobRevision is None)
        self._onlineStatus.setText(
            _tr("Select a game, choose packs, then Download & load."))
        self._rebuildPackChecks()

    def _onIndexFailed(self, err):
        self._onlineStatus.setText(
            _tr("Could not load the game list (check your connection).")
            + f"\n{err}")

    def _selectedGameCode(self):
        idx = self._gameCombo.currentIndex()
        return self._gameCombo.itemData(idx) if idx >= 0 else None

    def _rebuildPackChecks(self, *_):
        # Clear existing checkboxes.
        for cb in self._packChecks:
            cb.setParent(None)
        self._packChecks = []
        game = self._games.get(self._selectedGameCode())
        if not game:
            return
        # Only the functional packs are offered (core's ALLOWED_PACKS).
        for pack in game["packs"]:
            cb = QCheckBox(
                f"{pack['name']}  ({self._humanSize(pack['size'] or 0)})")
            cb.setProperty("pack_name", pack["name"])
            cb.setChecked(pack["default"])
            self._packsLayout.addWidget(cb)
            self._packChecks.append(cb)

    @staticmethod
    def _humanSize(n):
        f = float(n)
        for unit in ("B", "KB", "MB", "GB"):
            if f < 1024 or unit == "GB":
                return f"{f:.0f} {unit}" if unit in ("B", "KB") else f"{f:.1f} {unit}"
            f /= 1024

    def _startDownload(self):
        code = self._selectedGameCode()
        game = self._games.get(code)
        if not game:
            return
        sizes = {pack["name"]: pack["size"] or 0 for pack in game["packs"]}
        selected = [cb.property("pack_name") for cb in self._packChecks
                    if cb.isChecked() and cb.property("pack_name") in sizes]
        if not selected:
            QMessageBox.information(
                self, _tr("Download online"), _tr("Select at least one pack."))
            return
        try:
            # The list shown is the cached index's: no fetch on this thread.
            started, revision = AssetPackService.instance.Install(
                code, selected, fetch=False)
        except assets.AssetPackError as error:
            self._downloadFailed(error.message)
            return
        if started == assets.BUSY:
            self._downloadFailed("an asset job is running already")
            return

        total = sum(sizes[name] for name in selected)
        self._progressDialog = QProgressDialog(
            _tr("Preparing…"), _tr("Cancel"), 0, max(1, total // 1024), self)
        self._progressDialog.setWindowModality(Qt.WindowModality.WindowModal)
        self._progressDialog.setMinimumDuration(0)
        self._progressDialog.setAutoClose(False)
        self._progressDialog.setAutoReset(False)
        self._progressDialog.canceled.connect(
            lambda: AssetPackService.instance.Cancel())
        self._label = ""
        self._startJob(revision, "download")
        self._progressDialog.show()

    def _downloadFailed(self, message):
        QMessageBox.warning(
            self, _tr("Download failed"),
            _tr("Could not download the assets.") + f"\n{message}")
        self._onlineStatus.setText(_tr("Download failed."))

    def _showDownload(self, job):
        dialog = self._progressDialog
        if dialog is None or dialog.wasCanceled():
            # Cancelled: hidden until the job stops (at its next chunk, or
            # once a pack's extraction is over); a setValue would show it.
            return
        if job["stage"] == assets.DOWNLOAD and job["file"]:
            label = f"{job['game']} / {job['pack']}: {job['file']}"
        elif job["stage"] == assets.EXTRACT:
            label = f"{job['game']} / {job['pack']}: extracting…"
        else:
            label = self._label
        if label and label != self._label:
            self._label = label
            dialog.setLabelText(label)
        totalKiB = max(1, job["total"] // 1024)
        dialog.setMaximum(totalKiB)
        # setValue processes events on a modal dialog: _onPoll's guard keeps
        # the timer from re-entering (agent-pitfalls #9).
        dialog.setValue(min(job["done"] // 1024, totalKiB))

    def _onDownloadDone(self, job):
        if self._progressDialog:
            self._progressDialog.close()
            self._progressDialog = None
        if job["status"] == assets.CANCELLED:
            self._onlineStatus.setText(_tr("Download cancelled."))
            return
        if job["status"] == assets.ERROR:
            if job["error"] == assets.STOPPED:
                return  # the app is shutting down
            self._downloadFailed(job["message"] or job["error"])
            return
        # The job has scanned the games: the selector has it.
        self._onlineStatus.setText(
            _tr("Downloaded and loaded: ") + (job["game"] or ""))

    # ── Offline import ───────────────────────────────────────────────────
    def _buildImportGroup(self):
        group = QGroupBox(_tr("Import from a folder or ZIP"))
        v = QVBoxLayout()

        row = QHBoxLayout()
        self._btImportFolder = QPushButton(_tr("Import from folder"))
        self._btImportFolder.clicked.connect(self._importFromFolder)
        row.addWidget(self._btImportFolder)

        self._btImportZip = QPushButton(_tr("Import from ZIP"))
        self._btImportZip.clicked.connect(self._importFromZip)
        row.addWidget(self._btImportZip)
        row.addStretch(1)
        v.addLayout(row)

        self._importStatus = QLabel("")
        self._importStatus.setWordWrap(True)
        self._importStatus.setStyleSheet("color: #6aa84f;")
        v.addWidget(self._importStatus)

        group.setLayout(v)
        return group

    def _importFromFolder(self):
        src = QFileDialog.getExistingDirectory(
            self, _tr("Select a game folder to import"))
        if not src:
            return
        try:
            games = assets.ProbeFolder(src)
        except assets.AssetPackError as error:
            self._importRefused(error.code, error.message)
            return
        overwrite = self._askOverwrite(
            [name for name, _folder in games], assets.ImportConflicts(games))
        if overwrite is None:
            return
        self._startImport(
            lambda: AssetPackService.instance.ImportFolder(src, overwrite))

    def _importFromZip(self):
        path, _filter = QFileDialog.getOpenFileName(
            self, _tr("Select a game ZIP to import"), "", "ZIP (*.zip)")
        if not path:
            return
        try:
            # A game at the ZIP's top is named after the file.
            names = assets.ProbeZip(path)
        except assets.AssetPackError as error:
            self._importRefused(error.code, error.message)
            return
        overwrite = self._askOverwrite(names, assets.ImportConflicts(names))
        if overwrite is None:
            return
        self._startImport(
            lambda: AssetPackService.instance.ImportZip(path, overwrite))

    def _askOverwrite(self, names, conflicts):
        """Of the import's game `names`, the installed ones (`conflicts`) the
        operator agrees to replace, asked one by one as before; None (and
        "Nothing imported.") when every game is installed and every one was
        declined."""
        overwrite = set()
        for name in conflicts:
            ans = QMessageBox.question(
                self, _tr("Overwrite?"),
                name + "\n" + _tr("This game already exists. Overwrite it?"))
            if ans == QMessageBox.StandardButton.Yes:
                overwrite.add(name)
        if all(name in conflicts and name not in overwrite
               for name in names):
            self._importStatus.setText(_tr("Nothing imported."))
            return None
        return overwrite

    def _startImport(self, start):
        try:
            started, revision = start()
        except assets.AssetPackError as error:
            self._importRefused(error.code, error.message)
            return
        if started == assets.BUSY:
            self._importRefused(None, "an asset job is running already")
            return
        # The import runs as a job: the window stays usable (its job buttons
        # disabled), so no app-wide wait cursor.
        self._startJob(revision, "import")

    def _importRefused(self, code, message):
        if code == assets.NO_GAME:
            text = _tr("No game found. A game folder must contain "
                       "base_files/config.json.")
        elif code == assets.NOT_A_ZIP:
            text = _tr("The selected file is not a valid ZIP.")
        elif code in (assets.UNSAFE_ARCHIVE, assets.TOO_LARGE):
            text = _tr("The selected file is not a valid ZIP.") + \
                f"\n{message}"
        else:
            text = _tr("Could not copy the game files.") + f"\n{message}"
        QMessageBox.warning(self, _tr("Import failed"), text)

    def _onImportDone(self, job):
        if job["status"] == assets.ERROR:
            if job["error"] == assets.STOPPED:
                return  # the app is shutting down
            self._importRefused(job["error"], job["message"])
        for failure in job["failed"]:
            QMessageBox.warning(
                self, _tr("Import failed"),
                _tr("Could not copy the game files.")
                + f"\n{failure['message']}")
        if not job["imported"]:
            self._importStatus.setText(_tr("Nothing imported."))
            return
        # The job has scanned the games: the selector has them.
        self._importStatus.setText(
            _tr("Imported: ") + ", ".join(job["imported"]))

    # ── The job ──────────────────────────────────────────────────────────
    def _startJob(self, revision, kind):
        self._jobRevision = revision
        self._jobKind = kind
        self._setJobButtons(False)
        self._poll.start()

    def _onJobLost(self, kind, revision):
        logger.warning(
            f"Add game assets: asset job #{revision} started before the "
            "window read its own job's outcome")
        text = _tr("The result is unknown: another asset job has started.")
        if kind == "download":
            if self._progressDialog:
                self._progressDialog.close()
                self._progressDialog = None
            self._onlineStatus.setText(text)
        else:
            self._importStatus.setText(text)

    def _setJobButtons(self, enabled):
        self._btDownload.setEnabled(enabled and self._gameCombo.count() > 0)
        self._btImportFolder.setEnabled(enabled)
        self._btImportZip.setEnabled(enabled)

    def _onPoll(self):
        if self._polling:
            return
        self._polling = True
        try:
            future = self._indexFuture
            if future is not None and future.done():
                self._onIndexDone(future)
            if self._jobRevision is not None:
                job = AssetPackService.instance.Job()
                if job["revision"] != self._jobRevision:
                    # A newer job (revision > ours) started after the
                    # window's ended between two polls: one runs at a time,
                    # so the window's is over, its outcome no longer
                    # readable. Settled as unknown rather than polled for
                    # ever.
                    kind = self._jobKind
                    self._jobRevision = None
                    self._jobKind = None
                    self._setJobButtons(True)
                    self._onJobLost(kind, job["revision"])
                elif job["status"] == assets.RUNNING:
                    if self._jobKind == "download":
                        self._showDownload(job)
                else:
                    kind = self._jobKind
                    self._jobRevision = None
                    self._jobKind = None
                    self._setJobButtons(True)
                    if kind == "download":
                        self._onDownloadDone(job)
                    else:
                        self._onImportDone(job)
            if self._indexFuture is None and self._jobRevision is None:
                self._poll.stop()
        except Exception:
            logger.opt(exception=True).error(
                "Add game assets: the progress could not be shown")
        finally:
            self._polling = False

    def _buildSourceRow(self):
        row = QHBoxLayout()
        btTsh = QPushButton(_tr("Open asset source (GitHub)"))
        btTsh.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(assets.SOURCE_URL)))
        row.addWidget(btTsh)

        btFolder = QPushButton(_tr("Open games folder"))
        btFolder.clicked.connect(self._openGamesFolder)
        row.addWidget(btFolder)
        row.addStretch(1)
        return row

    def _openGamesFolder(self):
        path = os.path.abspath(assets.GAMES_DIR)
        os.makedirs(path, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
