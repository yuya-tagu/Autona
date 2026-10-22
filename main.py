#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Disable .pyc generation before any package imports so a stale
# __pycache__ can never shadow freshly-edited source files. Was a
# common debugging footgun: Edit tool would rewrite a .py without
# updating its mtime, Python would reuse the cached bytecode, and
# the running app would execute code that no longer matched disk.
import sys
sys.dont_write_bytecode = True

# Reference point for every "STARTUP phase=..." marker. Taken before any
# real work so the elapsed times are measured from process start.
import time
_STARTUP_T0 = time.perf_counter()

# Select the Qt binding BEFORE `from src import Autona` (which imports qtpy)
# and before qasync is imported — both resolve the binding at import time,
# and their auto-detection prefers PyQt over PySide if both are installed.
# PySide6 (LGPL v3) is required because Autona is distributed under MIT;
# PyQt6 (GPL v3) must not be used.
import os
os.environ["QT_API"] = "pyside6"

# Set the Windows AppUserModelID at the EARLIEST possible point — before
# `from src import Autona`, which assembles the app and (via its many
# imports) may lock the process/window identity Windows uses for the taskbar.
# Setting it here ensures the taskbar groups under our identity and uses the
# window icon (the Autona .ico) instead of the generic / host-exe icon.
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "taguyan.Autona")
    except Exception:
        pass

# Refuse to start while the external update helper (apply_update.ps1) is
# mirroring the new version over this very install dir. The helper relaunches
# the app itself when done; starting manually in that window would run a
# half-copied install and, worse, trigger CleanupUpdateLeftovers, which would
# delete the helper's staging dir mid-robocopy and lose the update (happened
# on the v1.3.0 E2E). The helper holds update_in_progress.lock with an
# exclusive share mode; the same check lives in src/AutonaUpdater.py
# (_UpdateInFlight) — duplicated here because reaching that module means
# `from src import Autona`, which assembles the whole app, and that is
# exactly what must not happen yet. Keep both in sync.
if getattr(sys, "frozen", False):
    def _update_in_flight():
        base = (os.environ.get("TEMP") or os.environ.get("TMP")
                or r"C:\Windows\Temp")
        lock = os.path.join(base, "autona_update", "update_in_progress.lock")
        if not os.path.exists(lock):
            return False
        try:
            os.remove(lock)   # held share-none by the helper -> OSError
            return False      # deletable = stale lock from a crashed helper
        except OSError:
            return True

    def _focus_install_window():
        """Bring apply_update.ps1's install window to the front. Best-effort:
        the title is the contract between the two (UI_TITLE over there)."""
        try:
            import ctypes
            hwnd = ctypes.windll.user32.FindWindowW(None, "Autona Updater")
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    if _update_in_flight():
        # Exit immediately, and show NOTHING of our own. This process *is*
        # Autona.exe, and Windows locks a running image against writes, so for
        # as long as we are alive robocopy cannot replace the launcher: the
        # helper fails, restores the old exe, and the user reads that as the
        # updater silently rolling them back. A modal here is the worst case —
        # it kept this process alive for exactly as long as the user took to
        # read it. The helper already owns a visible install window, so just
        # surface that and get out of the way.
        _focus_install_window()
        sys.exit(0)

# ── Stage 0: single-instance gate ────────────────────────────────────────
# Autona owns exclusive resources (the web server port, out/ state files,
# global hotkeys, the OBS overlay), so a second launch must not start a
# second process — it hands the running one an "activate" request and exits
# without showing anything. A Win32 named mutex is used rather than a lock
# file because the OS releases it when the process dies, so a crash can
# never leave a stale lock behind that blocks the next launch.
_MUTEX_NAME = "taguyan.Autona.SingleInstance"
_IPC_SERVER_NAME = "taguyan.Autona.ipc"
_ERROR_ALREADY_EXISTS = 183

# Held for the lifetime of the process on purpose: the mutex lives as long
# as its handle, so this must not be closed or garbage collected.
_instanceMutex = None


def _acquire_single_instance():
    """True when this process is the one and only Autona instance."""
    global _instanceMutex
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        # use_last_error=True makes ctypes snapshot GetLastError() right
        # after the call, so nothing running in between can clobber the
        # ERROR_ALREADY_EXISTS this whole gate hinges on. restype must be
        # spelled out too: the default c_int truncates a 64-bit HANDLE.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        err = ctypes.get_last_error()
        if not handle:
            # Cannot arbitrate — start normally rather than refusing to run.
            return True
        if err == _ERROR_ALREADY_EXISTS:
            return False
        _instanceMutex = handle
        return True
    except Exception:
        return True


def _activate_running_instance():
    """Tell the running instance to come to the front, then leave quietly."""
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtNetwork import QLocalSocket
    # QLocalSocket's waitFor* calls need a thread event dispatcher, which
    # only exists once a QCoreApplication has been constructed. This is the
    # cheap non-GUI one and the process exits moments later.
    if QCoreApplication.instance() is None:
        QCoreApplication(sys.argv)
    # The winner may still be between taking the mutex and listening, so
    # retry briefly. If it never answers we still exit silently: the mutex
    # already proved another instance owns this machine, and a dialog here
    # would be noise (same "step aside quietly" rule as the update gate).
    for _ in range(3):
        sock = QLocalSocket()
        sock.connectToServer(_IPC_SERVER_NAME)
        if sock.waitForConnected(500):
            sock.write(b"activate")
            sock.waitForBytesWritten(500)
            sock.disconnectFromServer()
            return
        sock.abort()


if not _acquire_single_instance():
    _activate_running_instance()
    sys.exit(0)

import autona_splash

autona_splash.set_startup_t0(_STARTUP_T0)
autona_splash.log_startup_phase("gate")

# ── Stage 1: early feedback ──────────────────────────────────────────────
# QApplication and the splash are built HERE, before `from src import Autona`:
# that import chain assembles the whole app (bootstrap_core() constructs every
# singleton) and takes tens of seconds, and until this change nothing at all
# was drawn while it ran — the dead window after a double click is what drove
# users to click again (and start a second process).
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

# Must precede QApplication construction — the QWebEngineView instances
# built later in startup require it.
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
App = QApplication(sys.argv)

# The splash is kept alive by autona_splash.ACTIVE_SPLASH; Window.__init__
# picks it up from there to push progress and finish() it.
autona_splash.create_splash()
App.processEvents()
autona_splash.log_startup_phase("splash")

# ── Stage 0 (cont.): serve activation requests from later launches ───────
_ipcServer = None
_activationPending = False
_activationTarget = None


def _bring_to_front(window):
    window.showNormal()      # undo a minimized state
    window.raise_()
    window.activateWindow()


def _request_activation():
    global _activationPending
    if _activationTarget is None:
        # A second launch can land while the main window is still being
        # built; remember it and honour it once there is a window.
        _activationPending = True
        return
    _bring_to_front(_activationTarget)


def _set_activation_target(window):
    global _activationTarget, _activationPending
    _activationTarget = window
    if _activationPending:
        _activationPending = False
        _bring_to_front(window)


def _on_ipc_connection():
    conn = _ipcServer.nextPendingConnection()
    if conn is None:
        return
    if conn.bytesAvailable() or conn.waitForReadyRead(500):
        if bytes(conn.readAll()).startswith(b"activate"):
            _request_activation()
    conn.disconnectFromServer()
    conn.deleteLater()


def _start_ipc_server():
    global _ipcServer
    from PySide6.QtNetwork import QLocalServer
    # A hard kill leaves the named pipe/socket behind; clearing it first
    # keeps listen() from failing after a crash.
    QLocalServer.removeServer(_IPC_SERVER_NAME)
    server = QLocalServer()
    if not server.listen(_IPC_SERVER_NAME):
        # Non-fatal: without it a second launch simply exits without
        # raising this window, which still beats a second process.
        return
    # Published before connecting the signal: the handler reads this global.
    _ipcServer = server
    server.newConnection.connect(_on_ipc_connection)


_start_ipc_server()

# The heavy import: `src` itself is an empty package (importing it does
# nothing), so the application assembly module is named explicitly. This line
# is where logger configuration, bootstrap_core() and the Window definition
# actually run — keep it after the gate and the splash.
from src import Autona
autona_splash.log_startup_phase("import-src")

import multiprocessing
import signal
import asyncio
from qasync import run, QEventLoop
from functools import partial
from src.Helpers.AutonaWindowsShortcut import ensure_appid_shortcut


async def main(event_loop):
    def close_future(future, loop):
        loop.call_later(10, future.cancel)
        future.cancel()

    future = asyncio.Future()

    window = Autona.Window()
    # From here on a second launch can be answered with a real window.
    _set_activation_target(window)
    if hasattr(Autona.App, "aboutToQuit"):
        getattr(Autona.App, "aboutToQuit").connect(
            partial(close_future, future, event_loop))

    try:
        event_loop.add_signal_handler(signal.SIGINT, lambda: window.close())
        event_loop.add_signal_handler(signal.SIGTERM, lambda: window.close())
    except NotImplementedError:  # windows...
        pass

    await future
    if isinstance(future.result(), int):
        return future.result()

    return 0


if __name__ == '__main__':
    # Pyinstaller fix
    multiprocessing.freeze_support()

    # Register the AppID-tagged Start Menu shortcut so Win11 resolves the
    # taskbar icon authoritatively (the window HICON alone is an unreliable
    # fallback). Must run before any window is shown; runs only in the real
    # main process (after freeze_support, inside __main__).
    ensure_appid_shortcut()

    try:
        try:
            loop = QEventLoop()
            asyncio.set_event_loop(loop)
            sys.exit(loop.run_until_complete(main(loop)))
        except asyncio.exceptions.CancelledError:
            sys.exit(0)
    except RuntimeError:
        sys.exit(0)

