"""Regression tests for GuiExecutor.run_sync cross-thread marshalling.

PySide6 copies dict-typed signal arguments (QVariantMap) on queued
connections, which silently broke the old result-dict handoff after the
PyQt6 -> PySide6 migration (KeyError: 'result'). These tests pin the
closure-based implementation: results and exceptions raised on the GUI
thread must reach a caller waiting on a worker thread.
"""
import os
import threading
import time

os.environ.setdefault("QT_API", "pyside6")

from qtpy.QtCore import QCoreApplication

from src.Helpers import AutonaQtHelper as helper


def _get_app():
    return QCoreApplication.instance() or QCoreApplication([])


def _run_in_worker(app, fn, timeout=5.0):
    """Call fn on a worker thread while pumping the Qt event loop here."""
    outcome = {}

    def worker():
        try:
            outcome["result"] = fn()
        except Exception as e:
            outcome["exc"] = e

    thread = threading.Thread(target=worker)
    thread.start()
    deadline = time.monotonic() + timeout
    while thread.is_alive() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    thread.join(1.0)
    assert not thread.is_alive(), "worker deadlocked waiting for run_sync"
    return outcome


def test_run_sync_returns_result_from_worker_thread():
    app = _get_app()
    if helper.gui_executor is None:
        helper.init_gui_executor()

    outcome = _run_in_worker(
        app,
        lambda: helper.gui_executor.run_sync(
            lambda: threading.current_thread().name),
    )

    # fn must have executed on the GUI (main) thread and its return value
    # must reach the worker — the exact path that raised KeyError before.
    assert outcome.get("result") == threading.current_thread().name


def test_run_sync_reraises_exception_on_caller_thread():
    app = _get_app()
    if helper.gui_executor is None:
        helper.init_gui_executor()

    def boom():
        raise ValueError("expected failure")

    outcome = _run_in_worker(
        app, lambda: helper.gui_executor.run_sync(boom))

    assert isinstance(outcome.get("exc"), ValueError)
    assert str(outcome["exc"]) == "expected failure"


def test_run_sync_from_gui_thread_runs_inline():
    _get_app()
    if helper.gui_executor is None:
        helper.init_gui_executor()

    # AutoConnection on the GUI thread is a direct call — no event loop
    # pumping needed, and no deadlock.
    assert helper.gui_executor.run_sync(lambda: 42) == 42
