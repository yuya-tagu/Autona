import functools
import os
import threading

from loguru import logger
from qtpy.QtCore import *


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


class NoIconProxyModel(QSortFilterProxyModel):
    """Proxy model that strips icons (DecorationRole) from the source model.

    One definition for every combo that lists a shared asset model as text
    only (the player card, the RESULT and TOP 8 panels). It lives here rather
    than in one of those widget modules so that using it never means
    importing another panel.
    """
    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DecorationRole:
            return None
        return super().data(index, role)


class HeldBackText:
    """Which finished edits of a text field are the operator's, for a widget
    that renders its fields from the state (ADR-005 S2a, S3e-2).

    A render never redraws a field the operator is in. What it leaves there
    is *held back* -- finishing the field with it (a focus-out, Enter,
    typing that ends where it started) writes nothing, and the field shows
    the state once it is left -- in two cases:

    - it is the text the field last showed in step with the state (rendered,
      or the field's own edit as written), and the state has moved on from
      it: writing it would put the old value back over the new one.
    - the operator was typing it when the field's own value was written
      elsewhere (the Web, a set load). That write wins over the typing in
      progress (ADR-005 S2d). Only the first render to see that write holds
      the typing back; what the operator types after it is theirs.

    Anything else the operator typed is their edit, written when they finish
    it, however many renders ran meanwhile -- a render that left the field's
    own value where it was (a point scored, another field written) changes
    nothing about it. Until S3e-2 the player cards and the team names held
    any typing back, so such a render lost what the operator had typed.

    Not `isModified()`: typing that ends where it started is a keystroke,
    and would write the old value back (S2a).

    One rule for the player cards, the boards' team names, the crew board
    and the commentary panel. Keyed by whatever names a field to its owner
    (the widget, the field's state key, the team); a field never shown is
    taken to have shown nothing.
    """

    def __init__(self):
        self._shown = {}
        self._heldBack = {}
        # The field's state value as the last render that left it alone saw
        # it: a write of the field is "seen" once.
        self._seenState = {}

    def Shown(self, key, text):
        """The field shows `text` in step with the state: a render set it or
        found it there, or the field's own edit of it was written."""
        self._heldBack.pop(key, None)
        self._seenState.pop(key, None)
        self._shown[key] = text

    def LeftAlone(self, key, text, stateText):
        """A render left `text` in the field because the operator was in it;
        the state holds `stateText` for it."""
        shown = self._shown.get(key, "")
        if text == shown:
            self._heldBack[key] = text
        elif stateText != shown and stateText != self._seenState.get(key):
            self._heldBack[key] = text
        self._seenState[key] = stateText

    def IsHeldBack(self, key, text) -> bool:
        """`text` is what a render left in the field over a newer value."""
        return key in self._heldBack and self._heldBack[key] == text

    def IsEdit(self, key, text, stateHolds) -> bool:
        """Whether finishing the field with `text` is the operator's edit, to
        be written now (True records it as shown). `stateHolds`: the state
        already holds `text` -- nothing to write either."""
        if stateHolds:
            self.Shown(key, text)
            return False
        if self.IsHeldBack(key, text):
            return False
        self.Shown(key, text)
        return True

