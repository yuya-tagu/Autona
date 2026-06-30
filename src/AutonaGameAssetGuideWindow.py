"""Dialog for adding game assets.

Autona does not bundle character/stage images because they are copyrighted by
their respective rights holders (see user_data/games/README.md). This window
lets the operator obtain them WITHOUT installing TournamentStreamHelper:

  * Online: download asset packs directly from the public upstream source that
    TSH itself uses — joaorb64/StreamHelperAssets. The index (assets.json)
    lists each game's packs; the binaries live on the repo's "latest" GitHub
    Release as split 7z files. We fetch the selected packs, concatenate the
    split parts, extract with py7zr into user_data/games/<game>/, then re-scan.
  * Import: copy an already-downloaded game folder / ZIP (offline fallback).

After either path, AutonaGameAssetManager.instance.LoadGames() re-scans the
folder and emits onLoadAssets -> Window.ReloadGames(), so the game appears in
the selector live (no restart).

Autona itself neither bundles nor hosts these assets — they are fetched on
demand from the upstream public source at the operator's request (same posture
as TSH). User-facing strings go through QApplication.translate("app", ...);
Japanese strings live in Autona_ja.ts.
"""

import os
import shutil
import tempfile

import requests
from loguru import logger

from qtpy.QtCore import *
from qtpy.QtGui import *
from qtpy.QtWidgets import *

from . import ui_constants as UI


# Upstream project + its public asset source (the same source TSH downloads
# from). The index lists packs; binaries are split 7z on the latest Release.
_TSH_URL = "https://github.com/joaorb64/TournamentStreamHelper"
_ASSETS_INDEX_URL = (
    "https://raw.githubusercontent.com/joaorb64/StreamHelperAssets/main/assets.json")
_ASSETS_DOWNLOAD_BASE = (
    "https://github.com/joaorb64/StreamHelperAssets/releases/latest/download/")

# Allow-list of the only asset packs Autona offers / installs — the functional
# scoreboard assets the app actually consumes. Everything else (logos, key/mural
# art, promotional renders, posters, comics, animations, audio, versus-screen
# art, etc.) is NOT offered or installed.
#
# Rationale: publisher tournament guidelines restrict use of game logos,
# character/image illustrations and music/SFX for tournament promotion, venue
# decoration or BGM — e.g. Nintendo
# (https://www.nintendo.co.jp/tournament_guideline/index.html, Q2/A2). The
# upstream asset index carries no publisher field, so we cannot single out
# Nintendo titles; an allow-list applies the safe rule to ALL titles uniformly
# and stays correct as new pack types appear upstream.
_ALLOWED_PACKS = frozenset(
    {"base_files", "full", "portrait", "stage_icon", "variant_icon"})

# Of the allowed packs, these are checked by default in the online downloader.
_DEFAULT_PACKS = ("base_files", "full", "portrait", "stage_icon", "variant_icon")

# Writable game-assets folder, relative to CWD (same convention as
# AutonaGameAssetManager.LoadGames, which scans "./user_data/games/").
_GAMES_DIR = os.path.join("user_data", "games")


def _tr(text):
    return QApplication.translate("app", text)


def _find_game_dirs(root):
    """Return paths under `root` that look like a game folder, i.e. contain
    base_files/config.json. `root` itself counts; otherwise its immediate
    subfolders are scanned (a ZIP/folder may hold one or several games)."""
    if os.path.isfile(os.path.join(root, "base_files", "config.json")):
        return [root]
    found = []
    try:
        for name in sorted(os.listdir(root)):
            d = os.path.join(root, name)
            if os.path.isfile(os.path.join(d, "base_files", "config.json")):
                found.append(d)
    except OSError:
        pass
    return found


class _IndexFetchThread(QThread):
    """Fetch assets.json off the GUI thread."""
    ok = Signal(object)
    failed = Signal(str)

    def run(self):
        try:
            r = requests.get(_ASSETS_INDEX_URL, timeout=30)
            r.raise_for_status()
            self.ok.emit(r.json())
        except Exception as e:
            logger.warning(f"Asset index fetch failed: {e}")
            self.failed.emit(str(e))


class _AssetDownloadThread(QThread):
    """Download + extract the selected packs for one game.

    progress(done_kb, total_kb), message(text), done(error) — error "" on
    success, "cancelled" if the user aborted, otherwise the failure string.
    """
    progress = Signal(int, int)
    message = Signal(str)
    done = Signal(str)

    def __init__(self, game, packs, parent=None):
        super().__init__(parent)
        self.game = game          # game code (folder name)
        self.packs = packs        # list of (pack_name, pack_dict)
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        import py7zr  # lazy: heavy import only when actually downloading
        try:
            total = sum(
                int(f.get("size", 0))
                for _, p in self.packs
                for f in p.get("files", {}).values())
            total_kb = max(1, total // 1024)
            done = 0
            dest = os.path.abspath(os.path.join(_GAMES_DIR, self.game))
            os.makedirs(dest, exist_ok=True)
            tmp = tempfile.mkdtemp(prefix="autona_dl_")
            try:
                for pack_name, pack in self.packs:
                    parts = []
                    # Sorted so split parts (.7z.001/.002/...) concatenate in order.
                    for fn in sorted(pack.get("files", {}).keys()):
                        if self._cancel:
                            self.done.emit("cancelled")
                            return
                        self.message.emit(f"{self.game} / {pack_name}: {fn}")
                        local = os.path.join(tmp, fn)
                        r = requests.get(
                            _ASSETS_DOWNLOAD_BASE + fn,
                            stream=True, allow_redirects=True, timeout=60)
                        if r.status_code >= 400:
                            raise RuntimeError(f"HTTP {r.status_code} for {fn}")
                        with open(local, "wb") as out:
                            for chunk in r.iter_content(65536):
                                if self._cancel:
                                    self.done.emit("cancelled")
                                    return
                                out.write(chunk)
                                done += len(chunk)
                                self.progress.emit(min(done // 1024, total_kb), total_kb)
                        parts.append(local)

                    merged = os.path.join(tmp, pack_name + ".7z")
                    with open(merged, "wb") as out:
                        for part in parts:
                            with open(part, "rb") as pf:
                                shutil.copyfileobj(pf, out)
                    self.message.emit(f"{self.game} / {pack_name}: extracting…")
                    with py7zr.SevenZipFile(merged, "r") as z:
                        z.extractall(dest)
                    # Free temp space before the next (possibly large) pack.
                    for part in parts + [merged]:
                        try:
                            os.remove(part)
                        except OSError:
                            pass
                self.done.emit("")
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        except Exception as e:
            logger.error(f"Asset download failed: {e}")
            self.done.emit(str(e))


class AutonaGameAssetGuideWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._assets = None          # parsed assets.json
        self._indexThread = None
        self._dlThread = None
        self._progressDialog = None

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
        # operator looks at it.
        self._fetchIndex()

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
        self._btRetryIndex.clicked.connect(self._fetchIndex)
        btnRow.addWidget(self._btRetryIndex)
        btnRow.addStretch(1)
        v.addLayout(btnRow)

        group.setLayout(v)
        return group

    def _fetchIndex(self):
        if self._indexThread and self._indexThread.isRunning():
            return
        self._onlineStatus.setText(_tr("Loading game list…"))
        self._gameCombo.setEnabled(False)
        self._btDownload.setEnabled(False)
        self._indexThread = _IndexFetchThread(self)
        self._indexThread.ok.connect(self._onIndexLoaded)
        self._indexThread.failed.connect(self._onIndexFailed)
        self._indexThread.start()

    def _onIndexLoaded(self, data):
        self._assets = data if isinstance(data, dict) else {}
        self._gameCombo.clear()
        # Sort by display name; data = game code (folder name).
        rows = []
        for code, info in self._assets.items():
            if not isinstance(info, dict):
                continue
            name = info.get("name") or code
            rows.append((f"{name}  ({code})", code))
        for label, code in sorted(rows, key=lambda x: x[0].lower()):
            self._gameCombo.addItem(label, code)
        self._gameCombo.setEnabled(True)
        self._btDownload.setEnabled(self._gameCombo.count() > 0)
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
        code = self._selectedGameCode()
        if not code or not self._assets:
            return
        packs = (self._assets.get(code, {}) or {}).get("assets", {}) or {}
        for pack_name in sorted(packs.keys()):
            # Only the functional packs are offered (see _ALLOWED_PACKS).
            if pack_name not in _ALLOWED_PACKS:
                continue
            size = sum(int(f.get("size", 0))
                       for f in packs[pack_name].get("files", {}).values())
            cb = QCheckBox(f"{pack_name}  ({self._humanSize(size)})")
            cb.setProperty("pack_name", pack_name)
            cb.setChecked(pack_name in _DEFAULT_PACKS)
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
        if not code or not self._assets:
            return
        packs_index = (self._assets.get(code, {}) or {}).get("assets", {}) or {}
        selected = [
            (cb.property("pack_name"), packs_index[cb.property("pack_name")])
            for cb in self._packChecks
            if cb.isChecked() and cb.property("pack_name") in packs_index
            and cb.property("pack_name") in _ALLOWED_PACKS]
        if not selected:
            QMessageBox.information(
                self, _tr("Download online"), _tr("Select at least one pack."))
            return

        total = sum(int(f.get("size", 0))
                    for _, p in selected for f in p.get("files", {}).values())
        self._progressDialog = QProgressDialog(
            _tr("Preparing…"), _tr("Cancel"), 0, max(1, total // 1024), self)
        self._progressDialog.setWindowModality(Qt.WindowModality.WindowModal)
        self._progressDialog.setMinimumDuration(0)
        self._progressDialog.setAutoClose(False)
        self._progressDialog.setAutoReset(False)

        self._dlThread = _AssetDownloadThread(code, selected, self)
        self._dlThread.progress.connect(self._onDlProgress)
        self._dlThread.message.connect(self._progressDialog.setLabelText)
        self._dlThread.done.connect(self._onDlDone)
        self._progressDialog.canceled.connect(self._dlThread.cancel)
        self._btDownload.setEnabled(False)
        self._dlThread.start()
        self._progressDialog.show()

    def _onDlProgress(self, done_kb, total_kb):
        if self._progressDialog:
            self._progressDialog.setMaximum(total_kb)
            self._progressDialog.setValue(done_kb)

    def _onDlDone(self, error):
        if self._progressDialog:
            self._progressDialog.close()
            self._progressDialog = None
        self._btDownload.setEnabled(True)
        if error == "cancelled":
            self._onlineStatus.setText(_tr("Download cancelled."))
            return
        if error:
            QMessageBox.warning(
                self, _tr("Download failed"),
                _tr("Could not download the assets.") + f"\n{error}")
            self._onlineStatus.setText(_tr("Download failed."))
            return
        self._reloadGames()
        self._onlineStatus.setText(
            _tr("Downloaded and loaded: ") + (self._selectedGameCode() or ""))

    # ── Offline import ───────────────────────────────────────────────────
    def _buildImportGroup(self):
        group = QGroupBox(_tr("Import from a folder or ZIP"))
        v = QVBoxLayout()

        row = QHBoxLayout()
        btFolder = QPushButton(_tr("Import from folder"))
        btFolder.clicked.connect(self._importFromFolder)
        row.addWidget(btFolder)

        btZip = QPushButton(_tr("Import from ZIP"))
        btZip.clicked.connect(self._importFromZip)
        row.addWidget(btZip)
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
        if src:
            self._importSource(src)

    def _importFromZip(self):
        import zipfile
        path, _filter = QFileDialog.getOpenFileName(
            self, _tr("Select a game ZIP to import"), "", "ZIP (*.zip)")
        if not path:
            return
        tmp = tempfile.mkdtemp(prefix="autona_import_")
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                with zipfile.ZipFile(path) as z:
                    z.extractall(tmp)
            finally:
                QApplication.restoreOverrideCursor()
            self._importSource(tmp)
        except zipfile.BadZipFile:
            QMessageBox.warning(
                self, _tr("Import failed"), _tr("The selected file is not a valid ZIP."))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _importSource(self, src):
        game_dirs = _find_game_dirs(src)
        if not game_dirs:
            QMessageBox.warning(
                self, _tr("Import failed"),
                _tr("No game found. A game folder must contain "
                    "base_files/config.json."))
            return

        dest_root = os.path.abspath(_GAMES_DIR)
        os.makedirs(dest_root, exist_ok=True)

        imported = []
        for gd in game_dirs:
            name = os.path.basename(os.path.normpath(gd))
            dest = os.path.join(dest_root, name)
            if os.path.abspath(gd) == os.path.abspath(dest):
                imported.append(name)
                continue
            if os.path.exists(dest):
                ans = QMessageBox.question(
                    self, _tr("Overwrite?"),
                    name + "\n" + _tr("This game already exists. Overwrite it?"))
                if ans != QMessageBox.StandardButton.Yes:
                    continue
                shutil.rmtree(dest, ignore_errors=True)
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                shutil.copytree(gd, dest)
                # Keep only allowed asset packs. An asset pack is a subfolder
                # with its own config.json (how LoadGames detects packs); any
                # such pack not in _ALLOWED_PACKS (logos, renders, promo art,
                # audio, …) is removed even if present in the imported source.
                for child in os.listdir(dest):
                    cp = os.path.join(dest, child)
                    if (os.path.isdir(cp) and child not in _ALLOWED_PACKS
                            and os.path.isfile(os.path.join(cp, "config.json"))):
                        shutil.rmtree(cp, ignore_errors=True)
            except Exception as e:
                logger.error(f"Game asset import failed for {name}: {e}")
                QMessageBox.warning(
                    self, _tr("Import failed"),
                    _tr("Could not copy the game files.") + f"\n{e}")
                continue
            finally:
                QApplication.restoreOverrideCursor()
            imported.append(name)

        if not imported:
            self._importStatus.setText(_tr("Nothing imported."))
            return
        self._reloadGames()
        self._importStatus.setText(_tr("Imported: ") + ", ".join(imported))

    # ── Shared ───────────────────────────────────────────────────────────
    def _reloadGames(self):
        # Lazy import to avoid pulling the singleton chain at module load.
        from .AutonaGameAssetManager import AutonaGameAssetManager
        # Re-scan disk: repopulates games and refreshes the selector via the
        # onLoadAssets -> Window.ReloadGames() signal connection.
        AutonaGameAssetManager.instance.LoadGames()

    def _buildSourceRow(self):
        row = QHBoxLayout()
        btTsh = QPushButton(_tr("Open asset source (GitHub)"))
        btTsh.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(_TSH_URL)))
        row.addWidget(btTsh)

        btFolder = QPushButton(_tr("Open games folder"))
        btFolder.clicked.connect(self._openGamesFolder)
        row.addWidget(btFolder)
        row.addStretch(1)
        return row

    def _openGamesFolder(self):
        path = os.path.abspath(_GAMES_DIR)
        os.makedirs(path, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
