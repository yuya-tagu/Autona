import functools
import os
import threading

from loguru import logger
from qtpy.QtCore import *

from ..core.TaskExecutor import TaskExecutor


_STRICT_THREAD_ASSERT = os.environ.get("AUTONA_STRICT_THREAD_ASSERT", 0)
try:
    _STRICT_THREAD_ASSERT = int(_STRICT_THREAD_ASSERT)
except ValueError:
    logger.warning(f"Invalid value {_STRICT_THREAD_ASSERT} for _STRICT_THREAD_ASSERT. Defaulting to 0." )
    _STRICT_THREAD_ASSERT = 0


class GuiMarshalAborted(RuntimeError):
    """run_sync gave up waiting for a call the GUI thread had not started,
    because it never would (ADR-005 S3f-4): the app is shutting down (the
    TaskExecutor is stopping -- the GUI thread may be the one waiting in its
    shutdown for the caller) or the event loop has exited. The call never
    runs. Whoever waited treats it as cancelled."""


# How often a run_sync caller, while it waits, looks at whether the GUI
# thread will still run its call.
MARSHAL_POLL_SECS = 0.05

# Set once the application's event loop has exited (aboutToQuit, connected
# by init_gui_executor): nothing posted to the GUI thread runs after that.
_loopExited = threading.Event()


def gui_unavailable_reason():
    """Why a call posted to the GUI thread now will not run, or None.

    What run_sync gives up on (GuiMarshalAborted), and what a job about to
    start work that ends in such a call checks first, so that it does not
    begin -- write the state, say -- what it could not finish (the asset
    adapter's game load, the headless loader; ADR-005 S3f-4 review)."""
    if _loopExited.is_set():
        return "the event loop has exited"
    if QCoreApplication.instance() is None:
        return "there is no application"
    executor = TaskExecutor.instance
    if executor is not None and executor.stopping.is_set():
        return "the app is shutting down"
    return None


class GuiExecutor(QObject):
    _invoke = Signal(object)
    _later = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)

        # Autoconnection makes this almost-free if the gui thread uses it.
        self._invoke.connect(self._run, Qt.ConnectionType.AutoConnection)
        # Queued even when emitted on the GUI thread: later() is "once the
        # event loop is back", never "now".
        self._later.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _run(self, fn):
        fn()

    def __call__(self, fn):
        self._invoke.emit(fn)

    def later(self, fn):
        """Run `fn()` on the GUI thread once its event loop is back in
        control -- never right here, even when called on the GUI thread.
        Not waited for. For a caller that may not run the work where it is:
        a state observer, which runs inside StateManager.lock and must not
        write the state from there (core.StreamService, ADR-005 S4d)."""
        self._later.emit(fn)

    def run_sync(self, fn):
        """Run `fn()` on the GUI thread and return what it returns (or raise
        what it raised) here. On the GUI thread it runs right away.

        Elsewhere this waits for the GUI thread to run it -- unless it never
        will: once the app is shutting down (the TaskExecutor is stopping)
        or the event loop has exited, a call the GUI thread has not started
        yet is abandoned (it will not run, even if the GUI thread gets to it
        later) and GuiMarshalAborted is raised. A call already running there
        is waited for. Until ADR-005 S3f-4 the wait had no way out: a job
        waiting here after the event loop stopped never returned, and the
        interpreter's exit, which joins the executor's threads, hung on it.
        """
        # The result must travel back through a closure, never as a signal
        # argument: PySide6 marshals a `dict`-typed signal argument across a
        # queued connection as a QVariantMap *copy*, so anything the GUI
        # thread wrote into it never reached the caller (KeyError: 'result').
        # A callable emitted as `object` is passed by reference, so the
        # closure below shares `holder`/`done` with this thread directly.
        holder = {}
        done = threading.Event()
        # "queued" until one side claims the call: the GUI thread by starting
        # it ("running"), the waiter by giving up on it ("abandoned").
        claim = threading.Lock()
        phase = ["queued"]

        def task():
            with claim:
                if phase[0] == "abandoned":
                    return
                phase[0] = "running"
            try:
                holder["result"] = fn()
            except Exception as e:
                holder["exc"] = e
            finally:
                done.set()

        self._invoke.emit(task)
        while not done.wait(MARSHAL_POLL_SECS):
            reason = gui_unavailable_reason()
            if reason is None:
                continue
            with claim:
                if phase[0] == "queued":
                    phase[0] = "abandoned"
                    raise GuiMarshalAborted(
                        f"the GUI thread will not run "
                        f"{getattr(fn, '__qualname__', fn)}: {reason}")
            # Already running on the GUI thread: it finishes there.

        if (exc := holder.get("exc", None)) is not None:
            raise exc
        return holder.get("result")


gui_executor: GuiExecutor = None
def init_gui_executor():
    global gui_executor
    gui_executor = GuiExecutor(None)
    # A marshal waiting once the event loop has exited gives up (run_sync).
    # aboutToQuit is emitted as the main event loop ends, on the GUI thread.
    app = QCoreApplication.instance()
    if app is not None:
        app.aboutToQuit.connect(_loopExited.set)


def gui_thread_sync(fn):
    """
    Helper decorator that automatically dispatches the target function to the gui thread. Note that this
    function will block the calling thread until enough GUI cycles have completed to process the dispatched event.
    This is useful if it is desirable for changes to propagate before progressing, but it is slower than the async
    fire-and-forget version of this method. Once the app is shutting down or the event loop has exited, a call the gui
    thread has not started is abandoned and GuiMarshalAborted is raised (GuiExecutor.run_sync).
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if gui_executor is None:
            raise ValueError("Can't marshal action to gui thread if the gui executor has not been initialized.")

        return gui_executor.run_sync(lambda: fn(*args, **kwargs))

    return wrapper


def gui_thread_async(fn):
    """
    The fire-and-forget sibling of gui_thread_sync: dispatches the target function to the gui thread and returns
    None at once, without waiting for it. On the gui thread itself the executor calls it directly, so there it still
    runs before the call returns. Nobody waits for an exception it raises, so it is logged where it happens.

    For work a background thread need not wait for -- an answer handed on, a result shown. A job of the core
    TaskExecutor that must wait for the gui thread uses gui_thread_sync / GuiExecutor.run_sync, which give up once the
    app is shutting down or the event loop has exited (ADR-005 S3f-4); a wait with no such way out would keep the
    process alive at exit.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if gui_executor is None:
            raise ValueError("Can't marshal action to gui thread if the gui executor has not been initialized.")

        def run():
            try:
                fn(*args, **kwargs)
            except Exception:
                logger.exception(f"{fn.__qualname__} failed on the gui thread")

        gui_executor(run)

    return wrapper


_app = None
def in_gui_thread() -> bool:
    # "The GUI thread" is the thread that owns the application object, i.e.
    # the one running the event loop GuiExecutor delivers to. That is a
    # QCoreApplication in the headless shell and a QApplication (a subclass)
    # on the desktop, so ask the base class (ADR-005 S3c). PySide6 happens to
    # answer the same through QApplication.instance() -- it is the inherited
    # static -- but this helper should not need QtWidgets to say so, nor
    # depend on what a binding returns for a subclass's instance().
    global _app
    if _app is None and (_app := QCoreApplication.instance()) is None:
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
