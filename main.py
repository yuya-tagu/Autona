#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Disable .pyc generation before any package imports so a stale
# __pycache__ can never shadow freshly-edited source files. Was a
# common debugging footgun: Edit tool would rewrite a .py without
# updating its mtime, Python would reuse the cached bytecode, and
# the running app would execute code that no longer matched disk.
import sys
sys.dont_write_bytecode = True

# Select the Qt binding BEFORE `import src` (which imports qtpy) and before
# qasync is imported — both resolve the binding at import time, and their
# auto-detection prefers PyQt over PySide if multiple bindings are installed.
# PySide6 (LGPL v3) is required for the closed-source binary distribution;
# PyQt6 (GPL v3) must not be used.
import os
os.environ["QT_API"] = "pyside6"

# Set the Windows AppUserModelID at the EARLIEST possible point — before
# `import src`, which constructs the QApplication and (via its many imports)
# may lock the process/window identity Windows uses for the taskbar. Setting
# it here ensures the taskbar groups under our identity and uses the window
# icon (the Autona .ico) instead of the generic / host-exe icon.
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "HiveeWorks.Autona")
    except Exception:
        pass

import src
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

    window = src.Window(event_loop)
    if hasattr(src.App, "aboutToQuit"):
        getattr(src.App, "aboutToQuit").connect(
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

