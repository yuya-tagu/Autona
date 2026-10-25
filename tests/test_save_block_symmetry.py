"""A failing handler must not switch off saving for the rest of the session.

`StateManager.saveBlocked` is a counter: SaveState only runs when it is back
at zero. So a code path that increments it and then dies before the matching
decrement does not lose one write -- it loses every write from that moment
on. `./out` stops updating and the WebSocket feed stops changing, with no
error visible anywhere in the UI. During a stream that is the whole overlay
frozen on the previous match.

BUG-3 (docs/changes/test-automation-strategy.md §3) was exactly this: the
best_of handler chained BlockSaving() ... ReleaseSaving() as elements of a
list built inside a lambda, so any exception in between skipped the release.
It was reachable -- an unloaded locale makes matchNames.get("best_of") return
None and .format() raise -- and it is the one place where a bug in a single
handler takes the entire output layer down with it.

These tests therefore assert two things after a handler blows up: that the
counter is back to zero, and that a later write really does reach ./out.
Only the second one is the user-visible contract; the first says why.
"""
import ast
from pathlib import Path

import pytest

from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"


# A key of its own rather than one of the board's: everything under score.{n}
# is a held subtree (the 反映 button), so those writes sit in the hold buffer
# and reaching ./out would prove nothing about SaveState having run.
PROBE_KEY = "test_save_block_probe"


def _probeFile(app):
    return app.dir / "out" / f"{PROBE_KEY}.txt"


def _breakBestOfFormatting(monkeypatch):
    """Break the best_of texts the way BUG-3's trigger did.

    An empty table is what AutonaLocaleHelper actually holds before
    LoadLocale() runs, so this is the real failure and not an injected one:
    matchNames.get("best_of") returns None and the .format() call on it
    raises AttributeError -- after best_of has been written and before
    first_to is. Since ADR-005 S2e core catches it and publishes the
    English default (AutonaActions._matchText), so the tests below pin that
    the broken locale still leaves saving unblocked;
    test_a_raising_write_inside_the_best_of_block_leaves_saving_unblocked
    makes something inside the block really raise.
    """
    monkeypatch.setattr(AutonaLocaleHelper, "matchNames", {})


# ── the happy path ───────────────────────────────────────────────────────

def test_best_of_change_leaves_saving_unblocked(autonaApp):
    board = autonaApp.board(1)

    board.bestOfSpin.setValue(5)
    autonaApp.settle()

    assert autonaApp.state.saveBlocked == 0


# ── the path that used to wedge it ───────────────────────────────────────

def test_a_failing_best_of_handler_leaves_saving_unblocked(
        autonaApp, monkeypatch):
    board = autonaApp.board(1)
    _breakBestOfFormatting(monkeypatch)

    # PySide6 reported the exception this raised inside the slot and carried
    # on, so setValue() returned normally and the traceback went to stderr --
    # which is precisely why the leaked counter was invisible in production.
    # Core falls back to the English texts since ADR-005 S2e.
    board.bestOfSpin.setValue(5)
    autonaApp.settle()

    assert autonaApp.state.saveBlocked == 0, (
        "the best_of handler kept saving blocked after raising; "
        "every later SaveState is suppressed")


def test_state_still_exports_after_a_failing_best_of_handler(
        autonaApp, monkeypatch):
    """The contract the counter assertion above stands in for."""
    board = autonaApp.board(1)
    state = autonaApp.state

    try:
        state.Set(PROBE_KEY, "before")
        autonaApp.settle()

        exported = _probeFile(autonaApp)
        assert exported.is_file(), "precondition: state should export at all"
        assert exported.read_text(encoding="utf-8") == "before"

        _breakBestOfFormatting(monkeypatch)
        board.bestOfSpin.setValue(5)
        autonaApp.settle()

        # Business as usual afterwards: the next write reaches the overlay.
        state.Set(PROBE_KEY, "after")
        autonaApp.settle()

        assert exported.read_text(encoding="utf-8") == "after", (
            "state stopped reaching ./out after the best_of handler raised")
    finally:
        # Shared process, shared state: leave nothing behind for the tests
        # that assert on the exported tree as a whole.
        state.Unset(PROBE_KEY)
        autonaApp.settle()


def test_the_widget_survives_a_failing_best_of_handler(autonaApp, monkeypatch):
    """...and the next, successful, change publishes normally."""
    board = autonaApp.board(1)

    with monkeypatch.context() as broken:
        _breakBestOfFormatting(broken)
        board.bestOfSpin.setValue(5)
        autonaApp.settle()

    board.bestOfSpin.setValue(3)
    autonaApp.settle()

    assert autonaApp.get("score.1.best_of") == 3
    assert autonaApp.get("score.1.first_to") == 2
    assert "3" in autonaApp.get("score.1.best_of_text")
    assert autonaApp.state.saveBlocked == 0


def test_a_raising_write_inside_the_best_of_block_leaves_saving_unblocked(
        autonaApp):
    """The formatter's failure no longer raises (see _breakBestOfFormatting),
    so the block is tested with a write inside it that does: the stage rows'
    rebuild. The exception reaches the caller -- SetBestOf is called
    directly now -- and the counter is back at zero on the way out."""
    from unittest.mock import patch

    from src.core.Actions import AutonaActions
    from src.core.GameTrackerService import GameTrackerService

    autonaApp.board(1)
    state = autonaApp.state
    try:
        with patch.object(GameTrackerService, "SetStageCount",
                          side_effect=RuntimeError("rows")):
            with pytest.raises(RuntimeError):
                AutonaActions.instance.SetBestOf(1, 5)
        assert state.saveBlocked == 0

        state.Set(PROBE_KEY, "after")
        autonaApp.settle()
        assert _probeFile(autonaApp).read_text(encoding="utf-8") == "after"
    finally:
        state.Unset(PROBE_KEY)
        autonaApp.settle()


# ── the structural rule, so the shape cannot come back ───────────────────

def test_the_best_of_handler_is_a_method_not_a_lambda_chain(autonaApp):
    """BlockSaving/ReleaseSaving as list elements is what caused BUG-3.

    A lambda returning `[BlockSaving(), Set(...), ..., ReleaseSaving()]` has
    no place to put a finally, so the pairing is only as safe as the code
    between the brackets. Pinning the handler as a real method keeps the
    `with StateManager.SaveBlock():` the only way to write it -- in core
    since ADR-005 S2e, where the six keys and the stage rows are written
    (AutonaActions.SetBestOf / RepublishBestOf); the spinbox's handler only
    asks for the change.
    """
    import inspect

    from src.AutonaScoreboardWidget import AutonaScoreboardWidget
    from src.core.Actions import AutonaActions

    def code(function):
        # Comments in there talk about the two calls by name; only real
        # code counts.
        return "\n".join(line for line in
                         inspect.getsource(function).splitlines()
                         if not line.lstrip().startswith("#"))

    for function in (AutonaActions.SetBestOf, AutonaActions.RepublishBestOf):
        assert "with StateManager.SaveBlock():" in code(function), function
    for function in (AutonaActions.SetBestOf, AutonaActions.RepublishBestOf,
                     AutonaActions._PublishBestOf,
                     AutonaScoreboardWidget._onBestOfChanged):
        assert "BlockSaving()" not in code(function), function
        assert "ReleaseSaving()" not in code(function), function


@pytest.mark.parametrize("boardNumber", [1, 2])
def test_best_of_handler_is_shared_by_both_scoreboards(autonaApp, boardNumber):
    """1v1 and team are the same class here; assert it rather than assume."""
    board = autonaApp.board(boardNumber)

    board.bestOfSpin.setValue(7)
    autonaApp.settle()

    assert autonaApp.get(f"score.{boardNumber}.best_of") == 7
    assert autonaApp.get(f"score.{boardNumber}.first_to") == 4
    assert autonaApp.state.saveBlocked == 0


# ── the same shape in Window.__init__ ────────────────────────────────────
#
# Window.__init__ opened the block on its first line and closed it ~600
# lines later, with nothing in between guaranteeing the release. A window
# that dies while building takes the launch with it, so the leaked counter
# was less damaging than BUG-3 -- but it is the identical shape, and the
# next person to add an early return or a try/except in there inherits it.
#
# src/Autona.py cannot be imported from a test: importing it IS the startup
# sequence (logger setup -> bootstrap_core() -> Window definition), see
# .claude/rules/agent-pitfalls.md #1. So this is read as source and checked
# with ast, the way tests/test_signal_lifetime.py does.

AUTONA_PY = SRC_ROOT / "Autona.py"


def _parseAutona():
    return ast.parse(AUTONA_PY.read_text(encoding="utf-8"), filename=str(AUTONA_PY))


def _isStateManagerCall(node, name):
    """`StateManager.<name>(...)` as an expression node."""
    return (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == name
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "StateManager")


def _functionsCalling(tree, name):
    """Names of the functions in `tree` containing a StateManager.<name>()."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(_isStateManagerCall(child, name) for child in ast.walk(node)):
            found.append(node.name)
    return found


def _windowInit(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Window":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                    return item
    raise AssertionError("Window.__init__ not found in src/Autona.py")


def test_window_init_builds_inside_a_save_block():
    """The construction call has to sit under `with SaveBlock():`.

    Not merely "a SaveBlock exists somewhere in __init__": the point is that
    whatever does the building is *inside* it, so an exception unwinds
    through the context manager's finally.
    """
    init = _windowInit(_parseAutona())

    blocks = [node for node in ast.walk(init)
              if isinstance(node, ast.With)
              and any(_isStateManagerCall(item.context_expr, "SaveBlock")
                      for item in node.items)]

    assert blocks, (
        "Window.__init__ no longer wraps its body in "
        "`with StateManager.SaveBlock():`; a failure part-way through "
        "construction would leave saving blocked for the whole session")
    assert any(isinstance(node, ast.Call) for block in blocks
               for stmt in block.body for node in ast.walk(stmt)), (
        "the SaveBlock in Window.__init__ is empty -- the window is being "
        "built outside it")


def test_no_hand_paired_block_saving_left_in_autona_py():
    """BlockSaving()/ReleaseSaving() by hand is what leaks the counter.

    Every block in this module goes through the context manager, so the
    calls should not appear at all. If a future change genuinely needs the
    raw pair, it has to be a try/finally -- and this test is the place to
    say so explicitly rather than to delete.
    """
    tree = _parseAutona()

    assert _functionsCalling(tree, "BlockSaving") == []
    assert _functionsCalling(tree, "ReleaseSaving") == []
