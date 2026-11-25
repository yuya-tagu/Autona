"""The native crash log (logs/tsh-crash.log): faulthandler's dump target.

The windowed exe has no console, so a native crash (an access violation in
Qt, a fatal error in the interpreter) leaves its only trace here. Both
shells open it at startup (src/Autona.py, src/headless.py).

- The previous run's dump is kept: a non-empty log is moved to
  logs/tsh-crash.prev.log (one generation) before the new one is opened,
  where `mode='w'` used to truncate it on the next start.
- faulthandler stays enabled until the log is closed by an atexit hook,
  which runs after the interpreter has joined the non-daemon threads (the
  ./out export and user_data/state.json), so a crash in that stretch is
  recorded too. atexit hooks run in reverse registration order and
  PySide6.QtCore registers its own before this one, so Qt/WebEngine
  cleanup can run after the close and is not covered. faulthandler is
  disabled before the file is closed, so a later fault can never be
  written to a reused fd number of an unrelated file.
- An empty log is removed at exit, as before.

Standard library only (no Qt, no loguru, nothing from src), and nothing
happens on import (pitfalls #1).
"""
import atexit
import faulthandler
import os
from pathlib import Path

PREVIOUS_NAME = "tsh-crash.prev.log"


def open_crash_log(path: Path):
    """Open `path` as faulthandler's dump target; the open file.

    A non-empty log the previous run left is moved to PREVIOUS_NAME next to
    it first (replacing an older one). Failing that -- another process has
    it open, say -- is not a reason to stop the start: the new log then
    replaces it. The file is closed (and removed when empty) at exit by
    close_crash_log, registered here.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.exists() and path.stat().st_size > 0:
            os.replace(path, path.with_name(PREVIOUS_NAME))
    except OSError:
        pass
    f = path.open(mode='w')
    faulthandler.enable(f)
    atexit.register(close_crash_log, f, path)
    return f


def close_crash_log(f, path: Path):
    """Stop dumping into `f`, close it, and remove `path` when nothing was
    written. Each step is on its own and nothing is raised: this runs at
    the very end of the process."""
    try:
        faulthandler.cancel_dump_traceback_later()
    except Exception:
        pass
    try:
        faulthandler.disable()
    except Exception:
        pass
    try:
        f.close()
    except Exception:
        pass
    try:
        if Path(path).stat().st_size == 0:
            Path(path).unlink()
    except Exception:
        pass


def arm_exit_watchdog(f, seconds=60):
    """Arm a dump of every thread's stack for a hung exit.

    If the process is still alive `seconds` from now -- the interpreter's
    exit join of the writer threads stuck, say -- faulthandler writes every
    thread's stack to `f`, the crash log open_crash_log returned (the
    process is not killed). The normal exit cancels it: close_crash_log
    runs at exit and calls faulthandler.cancel_dump_traceback_later().

    `f` is passed explicitly because dump_traceback_later does not use the
    file given to faulthandler.enable(): its default is sys.stderr, which
    the windowed app has replaced with a writer that has no file
    descriptor.
    """
    try:
        faulthandler.dump_traceback_later(seconds, exit=False, file=f)
    except Exception:
        pass
