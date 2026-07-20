import functools
import os
import threading

from loguru import logger
from qtpy.QtCore import *
from qtpy.QtWidgets import QApplication


_STRICT_THREAD_ASSERT = os.environ.get("AUTONA_STRICT_THREAD_ASSERT", 0)
try:
    _STRICT_THREAD_ASSERT = int(_STRICT_THREAD_ASSERT)
except ValueError:
    logger.warning(f"Invalid value {_STRICT_THREAD_ASSERT} for _STRICT_THREAD_ASSERT. Defaulting to 0." )
    _STRICT_THREAD_ASSERT = 0


class GuiExecutor(QObject):
    _invoke = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)

        # Autoconnection makes this almost-free if the gui thread uses it.
        self._invoke.connect(self._run, Qt.ConnectionType.AutoConnection)

    @Slot(object)
    def _run(self, fn):
        fn()

    def __call__(self, fn):
        self._invoke.emit(fn)

    def run_sync(self, fn):
        # The result must travel back through a closure, never as a signal
        # argument: PySide6 marshals a `dict`-typed signal argument across a
        # queued connection as a QVariantMap *copy*, so anything the GUI
        # thread wrote into it never reached the caller (KeyError: 'result').
        # A callable emitted as `object` is passed by reference, so the
        # closure below shares `holder`/`done` with this thread directly.
        holder = {}
        done = threading.Event()

        def task():
            try:
                holder["result"] = fn()
            except Exception as e:
                holder["exc"] = e
            finally:
                done.set()

        self._invoke.emit(task)
        done.wait()

        if (exc := holder.get("exc", None)) is not None:
            raise exc
        return holder.get("result")


gui_executor: GuiExecutor = None
def init_gui_executor():
    global gui_executor
    gui_executor = GuiExecutor(None)


def gui_thread_sync(fn):
    """
    Helper decorator that automatically dispatches the target function to the gui thread. Note that this
    function will block the calling thread until enough GUI cycles have completed to process the dispatched event.
    This is useful if it is desirable for changes to propagate before progressing, but it is slower than the async
    fire-and-forget version of this method.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if gui_executor is None:
            raise ValueError("Can't marshal action to gui thread if the gui executor has not been initialized.")

        return gui_executor.run_sync(lambda: fn(*args, **kwargs))

    return wrapper


_app = None
def in_gui_thread() -> bool:
    global _app
    if _app is None and (_app := QApplication.instance()) is None:
        return False

    return _app.thread().isCurrentThread()


def assert_gui_thread(fn=None):
    """Functions as either a decorator or a standalone function with no args."""

    def _assert():
        if not in_gui_thread():
            if _STRICT_THREAD_ASSERT != 0:
                raise RuntimeError("Failed gui thread assertion in strict mode.")
            else:
                try:
                    raise RuntimeError("stack snapshot")
                except RuntimeError:
                    logger.opt(exception=True).warning("Failed gui thread assertion in permissive mode.")

    if callable(fn):
        @functools.wraps(fn)
        def deco(*args, **kwargs):
            _assert()
            return fn(*args, **kwargs)
        return deco
    else:
        return _assert()

