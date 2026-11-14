"""The updater's dialogs open over the main window (ADR-005 S-final
SF-5b-1).

Until then the update check's answers -- "up to date" (_OnNoUpdate), the
check's error (_OnError) and the new version's dialog (_UpdateDialog) -- had
no parent, so they could open behind a window that is always on top.
Window._BuildUi hands itself over (AutonaUpdater.SetParent) once it is
built. The install's restart message box and its retry dialog
(UpdateProcedure) open over it too (the SF-5b-1 review), handed on by the
new version's dialog, and are freed once shown (the window owns them: the
re-review); what the install does, and in what order, is left as it was,
and so is the check.
"""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from qtpy.QtWidgets import QWidget

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def window(autonaApp):
    widget = QWidget()
    try:
        yield widget
    finally:
        widget.deleteLater()


@pytest.fixture
def updater(window):
    from src.AutonaUpdater import AutonaUpdater

    made = AutonaUpdater()
    made.SetParent(window)
    return made


@pytest.mark.parametrize("answer, box", [
    ("_OnNoUpdate", "information"),
    ("_OnError", "warning"),
])
def test_the_checks_answers_open_over_the_window(updater, window,
                                                 monkeypatch, answer, box):
    from src import AutonaUpdater as UpdaterModule

    parents = []
    monkeypatch.setattr(UpdaterModule.QMessageBox, box,
                        lambda parent, *args: parents.append(parent))
    handler = getattr(updater, answer)
    handler(*(["it failed"] if answer == "_OnError" else []))
    assert parents == [window]

    # A silent (startup) check still shows neither.
    updater._silent = True
    handler(*(["it failed"] if answer == "_OnError" else []))
    assert parents == [window]


def test_the_new_versions_dialog_opens_over_the_window(updater, window,
                                                       monkeypatch):
    from src import AutonaUpdater as UpdaterModule

    monkeypatch.setattr(UpdaterModule._UpdateDialog, "show",
                        lambda self: None)
    updater._OnNewVersion("1.0.0", "9.9.9", "https://example.invalid/r",
                          "https://example.invalid/a.zip", "notes")
    assert updater._dialog.parentWidget() is window


def test_with_no_window_yet_they_open_with_no_parent(autonaApp, monkeypatch):
    """Before the window hands itself over (nothing asks then: the startup
    check comes after it), the dialogs are what they were."""
    from src import AutonaUpdater as UpdaterModule
    from src.AutonaUpdater import AutonaUpdater

    parents = []
    monkeypatch.setattr(UpdaterModule.QMessageBox, "information",
                        lambda parent, *args: parents.append(parent))
    AutonaUpdater()._OnNoUpdate()
    assert parents == [None]


def test_the_window_hands_itself_to_the_updater_before_the_first_check():
    """In the window's construction (Window._BuildUi), ahead of the
    startup's silent check."""
    tree = ast.parse((ROOT / "src" / "Autona.py").read_text(encoding="utf-8"))
    build = next(node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef)
                 and node.name == "_BuildUi")
    lines = {}
    for node in ast.walk(build):
        if isinstance(node, ast.Call):
            lines.setdefault(ast.unparse(node), node.lineno)
    handed = lines.get("AutonaUpdater.instance.SetParent(self)")
    assert handed is not None, (
        "the window no longer hands itself to the updater")
    assert handed < lines["AutonaUpdater.instance.CheckForUpdates(silent=True)"]


def test_the_new_versions_dialog_hands_the_window_on_to_the_install(
        updater, window, monkeypatch):
    from src import AutonaUpdater as UpdaterModule

    handed = []
    monkeypatch.setattr(UpdaterModule._UpdateDialog, "show",
                        lambda self: None)
    monkeypatch.setattr(UpdaterModule, "UpdateProcedure",
                        lambda parent=None: handed.append(parent))
    updater._OnNewVersion("1.0.0", "9.9.9", "https://example.invalid/r",
                          "https://example.invalid/a.zip", "notes")
    updater._dialog._OnDownloadDone()
    assert handed == [window]


class _App:
    """QApplication, standing in for the install: the texts as they are,
    and the exits it asks for recorded (not the test run's)."""

    exits = []

    @staticmethod
    def translate(_context, text):
        return text

    @staticmethod
    def exit(*args):
        _App.exits.append(args)


@pytest.fixture
def install(window, tmp_path, monkeypatch):
    """UpdateProcedure as a frozen build runs it, with the payload's helper
    and staging prepared (a diff download's), nothing launched and nothing
    exited. Records, in order, the boxes it shows (('box' | 'retry',
    parent)) and the helper's launch."""
    from qtpy.QtWidgets import QDialog, QMessageBox

    from src import AutonaUpdater as UpdaterModule

    events = []

    class Box(QMessageBox):
        def exec(self):
            events.append(("box", self.parentWidget()))
            return 0

    class Retry(QDialog):
        def exec(self):
            events.append(("retry", self.parentWidget()))
            return 0

    (tmp_path / UpdaterModule._STAGING_MARKER_NAME).write_text("")
    (tmp_path / UpdaterModule.HELPER_SCRIPT).write_text("")
    _App.exits = []
    monkeypatch.setattr(UpdaterModule, "QApplication", _App)
    monkeypatch.setattr(UpdaterModule, "QMessageBox", Box)
    monkeypatch.setattr(UpdaterModule, "QDialog", Retry)
    monkeypatch.setattr(UpdaterModule, "_is_frozen", lambda: True)
    monkeypatch.setattr(UpdaterModule, "_UpdateInFlight", lambda: False)
    monkeypatch.setattr(UpdaterModule, "_install_dir", lambda: tmp_path)
    monkeypatch.setattr(UpdaterModule, "_update_workdir", lambda: tmp_path)
    monkeypatch.setattr(UpdaterModule, "_LaunchHelper",
                        lambda *args: events.append(("launch",)))
    return SimpleNamespace(module=UpdaterModule, events=events)


def test_the_installs_restart_box_opens_over_the_window(install, window):
    """Shown, then the helper launched, then the exit: the order is the
    one it had."""
    install.module.UpdateProcedure(window)
    assert install.events == [("box", window), ("launch",)]
    assert _App.exits == [()]


def test_the_installs_retry_dialog_opens_over_the_window_and_keeps_it(
        install, window, monkeypatch):
    """A failure preparing the update: the retry dialog over the window,
    and its Retry runs the install again over the same window."""
    from qtpy.QtWidgets import QPushButton

    failing = {"now": True}

    def inFlight():
        if failing["now"]:
            raise OSError("the workdir is locked")
        return False

    monkeypatch.setattr(install.module, "_UpdateInFlight", inFlight)
    made = []
    real = install.module.QDialog

    class Kept(real):
        def __init__(self, *args):
            super().__init__(*args)
            made.append(self)

    monkeypatch.setattr(install.module, "QDialog", Kept)
    install.module.UpdateProcedure(window)
    assert install.events == [("retry", window)]

    failing["now"] = False
    retry = next(button for button in made[0].findChildren(QPushButton)
                 if button.text() == "Retry")
    retry.click()
    assert install.events == [("retry", window), ("box", window),
                              ("launch",)]


def test_the_installs_boxes_are_freed_once_shown(install, window,
                                                monkeypatch):
    """Owned by the window, they would stay on it after exec -- one more
    on every Retry (the SF-5b-1 re-review): a retry dialog whose Retry is
    clicked while it is shown, then the restart box, leave none behind."""
    from qtpy.QtCore import QCoreApplication, QEvent
    from qtpy.QtWidgets import QDialog, QPushButton

    failing = {"now": True}

    def inFlight():
        if failing["now"]:
            failing["now"] = False  # the retry gets through
            raise OSError("the workdir is locked")
        return False

    monkeypatch.setattr(install.module, "_UpdateInFlight", inFlight)
    shown = install.module.QDialog

    class ClickedRetry(shown):
        def exec(self):
            # Shown, and Retry clicked while it is: the install runs again
            # inside this exec, as it does in the app.
            super().exec()
            next(button for button in self.findChildren(QPushButton)
                 if button.text() == "Retry").click()
            return 0

    monkeypatch.setattr(install.module, "QDialog", ClickedRetry)
    install.module.UpdateProcedure(window)
    assert install.events == [("retry", window), ("box", window),
                              ("launch",)]

    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert window.findChildren(QDialog) == []


def test_with_no_window_the_installs_boxes_have_no_parent(install):
    install.module.UpdateProcedure()
    assert install.events == [("box", None), ("launch",)]
