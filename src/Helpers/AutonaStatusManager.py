"""Cross-thread status broadcaster for the main window footer.

Workers call ``AutonaStatusManager.push("Foo を取得中...")`` to advertise
activity; the matching ``pop()`` (or context manager) removes it. The main
window subscribes to the ``changed`` signal and updates a ``QStatusBar``.

Multi-step operations (paginated fetches, per-character asset scans) can
pass ``progress``/``total`` so the footer can render a determinate
progress bar next to the message. ``total`` 0 means "no measurable
progress" — the footer then shows the message only.

Thread-safety: an internal ``threading.RLock`` protects the stack.
The ``changed`` signal is emitted with the latest entry, and Qt
auto-marshals to the GUI thread via the standard signal/slot machinery.
"""

import contextlib
import threading
from typing import Optional

from qtpy.QtCore import QObject, Signal


class _StatusSignals(QObject):
    # Emitted with (message, progress, total) that should currently be
    # shown. Empty message means "no active operation"; total 0 means
    # "no measurable progress".
    changed = Signal(str, int, int)


class AutonaStatusManager:
    signals = _StatusSignals()

    _lock = threading.RLock()
    # token -> (message, progress, total). Order preserved by dict
    # insertion semantics.
    _stack: "dict[int, tuple[str, int, int]]" = {}
    _next_token = 0

    @staticmethod
    def push(message: str, progress: int = 0, total: int = 0) -> int:
        """Register a new status. Returns a token to pass to ``pop``."""
        with AutonaStatusManager._lock:
            token = AutonaStatusManager._next_token
            AutonaStatusManager._next_token += 1
            AutonaStatusManager._stack[token] = (message, progress, total)
            current = (message, progress, total)
        AutonaStatusManager.signals.changed.emit(*current)
        return token

    @staticmethod
    def update(token: int, message: str,
               progress: int = 0, total: int = 0) -> None:
        """Replace the entry for an already-pushed token (e.g. progress)."""
        with AutonaStatusManager._lock:
            if token not in AutonaStatusManager._stack:
                return
            AutonaStatusManager._stack[token] = (message, progress, total)
            # Re-emit only if this token currently owns the visible slot.
            last_key = next(reversed(AutonaStatusManager._stack))
            if last_key != token:
                return
            current = (message, progress, total)
        AutonaStatusManager.signals.changed.emit(*current)

    @staticmethod
    def pop(token: Optional[int]) -> None:
        if token is None:
            return
        with AutonaStatusManager._lock:
            AutonaStatusManager._stack.pop(token, None)
            if AutonaStatusManager._stack:
                current = next(
                    reversed(AutonaStatusManager._stack.values()))
            else:
                current = ("", 0, 0)
        AutonaStatusManager.signals.changed.emit(*current)

    @staticmethod
    @contextlib.contextmanager
    def show(message: str):
        token = AutonaStatusManager.push(message)
        try:
            yield token
        finally:
            AutonaStatusManager.pop(token)
