"""The updater's dialogs open over the main window (ADR-005 S-final
SF-5b-1).

Until then the update check's answers -- "up to date" (_OnNoUpdate), the
check's error (_OnError) and the new version's dialog (_UpdateDialog) -- had
no parent, so they could open behind a window that is always on top.
Window._BuildUi hands itself over (AutonaUpdater.SetParent) once it is
built. The install's restart message box (_generate_restart_messagebox) is
left as it was, and so is everything the check and the update do.
"""
import ast
from pathlib import Path

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


def test_the_restart_message_box_is_left_as_it_was():
    """Not one of the three (SF-5b-1 leaves it for the hardware check)."""
    tree = ast.parse((ROOT / "src" / "AutonaUpdater.py").read_text(
        encoding="utf-8"))
    restart = next(node for node in ast.walk(tree)
                   if isinstance(node, ast.FunctionDef)
                   and node.name == "_generate_restart_messagebox")
    assert "QMessageBox()" in ast.unparse(restart)
